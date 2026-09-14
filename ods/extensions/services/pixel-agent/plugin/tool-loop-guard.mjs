// Pixel per-run tool-loop guard.
//
// OpenClaw's built-in identical-call detector blocks a repeated tool call, but
// a model can keep asking for the blocked tool on later continuation passes.
// Bound the web-research and coding-repair portions of a Pixel response. A
// duplicate fetch stays a source-specific rejection within that budget, while
// repeated foreground or background verification failures share a run-wide
// budget. Terminal blocks get one result in which to produce a useful final
// answer; if the model ignores one, abort only that active agent run through
// OpenClaw's public harness runtime.

import { createHash, randomBytes } from "node:crypto";
import * as fs from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { isIP } from "node:net";
import { isDeepStrictEqual } from "node:util";
import { projectWebResult } from "./web-result-projection.mjs";
import { createRunProgressBudget, failedToolOutcome, isLiteralEcho, RUN_PROGRESS_STOP_REASON } from "./run-progress-budget.mjs";
import { canonicalWorkspaceParams, extensionlessHtmlWrite, workspaceFileParent } from "./workspace-path-contract.mjs";

export const DEFAULT_WEB_TOOL_LIMITS = Object.freeze({
  search: 8,
  fetch: 24,
  total: 32,
  failedExecRetries: 3,
  failedVerificationAttempts: 6,
});

const MAX_COMPARE_SWAP_REPAIR_CHARS = 32_768;
const MAX_COMPARE_SWAP_REPAIRS_PER_PATH = 3;
const MAX_TRACKED_WORKSPACE_FILE_BYTES = 4 * 1024 * 1024;

export const WEB_BUDGET_EXHAUSTED_REASON =
  "The assistant's web-research budget is exhausted for this response. Do not call web tools again. Finish using the evidence already collected and any otherwise-authorized tools, including saving the requested report. Preserve existing evidence and clearly state any missing external information.";

export const WEB_SEARCH_BUDGET_EXHAUSTED_REASON =
  "The assistant's search-call allowance is exhausted for this response. Do not repeat web_search. Use web_fetch or targeted extraction for already identified public sources within the remaining page-reading and total allowances, or finish using collected evidence and otherwise-authorized tools, including saving the requested report.";

export const WEB_FETCH_BUDGET_EXHAUSTED_REASON =
  "The assistant's page-reading allowance is exhausted for this response. Do not repeat web_fetch, pixel_ods_web_extract or pixel_ods_research. Search may continue within its remaining search and total allowances. Finish using collected evidence and otherwise-authorized tools, including saving the requested report; do not claim unread pages were verified.";

export const WEB_LOOP_ABORT_REASON =
  "The assistant stopped this response because it requested another web tool after the bounded research budget was exhausted. Start a fresh message to continue with a narrower research question.";

export const WEB_LOOP_DELIVERY_REASON =
  "The assistant stopped a repeated web-research loop after reaching this response's research limit. It did not finish your request. The conversation and any saved files are preserved. You can ask the assistant to continue from the evidence already collected.";

export const WEB_FETCH_REPEAT_PIVOT_REASON =
  "The assistant already fetched this public page in this response. Avoid repeating that fetch. Use the returned evidence, target a missing detail with pixel_ods_web_extract, or choose another relevant source. Other authorized work may continue.";

export const WEB_FETCH_TRUNCATED_PIVOT_REASON =
  "The fetched public page was truncated. Only the returned content is evidence. Choose targeted extraction, another relevant source, or continue other authorized work; do not claim unread content was verified.";

export const WEB_FETCH_PUBLIC_ONLY_REASON =
  "The assistant blocked this fetch because web_fetch is restricted to public HTTP(S) hostnames and must not contact local, private, or raw-IP destinations. Do not retry that access through another tool. Other authorized work may continue, including approved ODS tools, public research, and saving verified findings.";

export const GITHUB_CANONICAL_SOURCE_PREFIX =
  "The assistant already has the owner's identified canonical public GitHub source:";

export const GITHUB_CANONICAL_FETCH_FAILED_REASON =
  "The attempted GitHub source was not fetched successfully. Other sources and authorized work remain available; distinguish unread information from verified findings.";

export const GITHUB_SOURCE_UNVERIFIED_DELIVERY_PREFIX =
  "The assistant did not successfully read a source belonging to the requested GitHub repository in this response. Repository claims remain unverified; the workspace and other collected evidence are preserved.";

export const EXEC_PRIVATE_NETWORK_REASON =
  "The assistant blocked this command because shell execution cannot be used to contact local, private, or raw-IP HTTP(S) destinations. Do not retry that access through another tool. Other authorized work may continue, including approved ODS tools, public research, and saving verified findings.";

export const PRIVATE_NETWORK_LOOP_ABORT_REASON =
  "The assistant stopped this response because it requested another tool after a private-network boundary was enforced. Start a fresh message with a safe public destination or an approved ODS status capability.";

export const PRIVATE_URL_REQUEST_REASON =
  "This request contains a private URL that the assistant cannot open from this chat. Do not call or substitute any tool, including ODS status or shell tools. Reply concisely that the private page was not accessed and ask the user to provide its content or use a separately approved private-access capability.";

export const CODING_RETRY_EXHAUSTED_REASON =
  "The assistant stopped a no-progress coding repair loop after its bounded failed-verification limit. Do not call another tool in this turn. Give the user a visible summary of the verified failure, the changes attempted, and the most useful next step.";

export const CODING_REPEAT_NO_PROGRESS_REASON =
  "That exact command already succeeded twice without a workspace mutation. Do not run it again. Perform the requested change with write, edit, or apply_patch, choose a materially different command, or give the owner a visible blocker.";

export const CODING_LOOP_ABORT_REASON =
  "The assistant stopped this response because it requested another coding tool after the repeated-command limit was reached. Start a fresh message to continue from the preserved workspace with a different approach.";

export const VISIBLE_REPLY_REQUIRES_FINAL_REASON =
  "Do not use a tool to deliver the reply and do not send a message to this same session. End the turn now with the requested text as the normal assistant response.";

export const EDIT_CREATE_REQUIRES_WRITE_REASON =
  "edit cannot create a new file because every edit replacement requires a non-empty oldText copied from existing content. Use the visible tool_call control now with id write and args containing the same path plus the exact newText as content. Do not retry edit.";

export const EDIT_CREATE_RETRY_EXHAUSTED_REASON =
  "The assistant blocked a repeated invalid attempt to create a file with edit. Do not call another tool in this turn. Tell the owner the file was not created and that a fresh retry must use write.";

export const EDIT_CREATE_LOOP_ABORT_REASON =
  "The assistant stopped this response because it kept retrying edit after the new-file write correction. The workspace is preserved; start a fresh message to retry with write.";

export const REPEATED_WRITE_REQUIRES_PATCH_REASON =
  "The write content matches what was previously recorded for that path in this turn. Use edit or apply_patch for the smallest relevant correction instead of rewriting the whole file with identical content; the file on disk may have been deleted or changed externally.";

export const REPEATED_WRITE_RETRY_EXHAUSTED_REASON =
  "The assistant blocked a second identical-content rewrite of that path after already directing a focused edit. Do not call another tool in this turn; start a fresh message and continue with edit or apply_patch.";

export const FOCUSED_EDIT_REQUIRED_REASON =
  "This edit repeats a large existing file in oldText and newText. Preserve context and make only the smallest unique replacements with edit, or use a focused apply_patch; do not resend the whole file.";

export const FOCUSED_EDIT_RETRY_EXHAUSTED_REASON =
  "The assistant blocked a second oversized whole-file edit after directing focused replacements. Do not call another tool in this turn. The existing file is preserved; start a fresh message and continue with small edit blocks or apply_patch.";

export const NOOP_EDIT_REQUIRES_CHANGE_REASON =
  "This edit makes no change because every oldText and newText pair is identical. Re-read the exact verification error already present in this turn and make one meaningful focused replacement. If the test asserts behavior the owner did not request, correct that test expectation; otherwise repair the implementation. Do not rerun verification until a real edit succeeds.";

export const NOOP_EDIT_RETRY_EXHAUSTED_REASON =
  "The assistant blocked a repeated no-op edit after explaining that identical replacement text cannot repair the failure. Do not call another tool in this turn. The workspace is preserved; start a fresh message and make one evidence-based focused change.";

export const PENDING_EXEC_REQUIRES_POLL_REASON =
  "That exact command is already running. Do not call exec again or start a replacement process. Use the visible tool_call control now with id process and args containing action poll plus the exact sessionId returned by the running command; continue polling that same session until it reaches a terminal result.";

export const PENDING_EXEC_RETRY_EXHAUSTED_REASON =
  "The assistant blocked another attempt to restart a command that is still running. Do not call exec again in this turn. Poll only the exact existing process session to a terminal result, then report its real output.";

export const PENDING_EXEC_LOOP_ABORT_REASON =
  "The assistant stopped this response because it kept restarting an already-running command instead of polling its process session. The original process was preserved for cancellation cleanup; start a fresh message to continue safely.";

export const VERIFICATION_PENDING_DELIVERY_PREFIX =
  "The assistant stopped before the verification process reached a terminal result, so success is unverified. The workspace is preserved; ask the assistant to continue the run or inspect the process.";

export const VERIFICATION_FAILED_DELIVERY_PREFIX =
  "The assistant could not complete this task successfully because the latest verification check failed. The workspace is preserved; ask the assistant to continue with a focused repair.";

export const VERIFICATION_NOT_RUN_DELIVERY_PREFIX =
  "The assistant could not complete this task successfully because the owner-requested verification was not executed. The workspace is preserved; ask the assistant to continue and run the requested checks.";

export const VERIFICATION_COMMAND_NOT_AUDITABLE_REASON =
  "The assistant blocked this verification because a shell pipeline, redirect, or chained command can hide the test runner's exit status or truncate its evidence. Rerun the same test command directly, with no pipeline, redirection, chaining, or output filter, and inspect its complete output.";

export const REQUESTED_UNITTEST_REQUIRED_REASON =
  "The owner explicitly requested Python unittest coverage, so that attempted file was not written. Make exactly one tool_call now with id write, the same path, and a complete replacement under 1000 characters. Begin with the needed imports including unittest; use one unittest.TestCase class with only the requested test_* methods and assertions; finish with unittest.main(). No narration, comments, docstrings, extra cases, or print-only custom runner. Do not run verification before this test file is accepted.";

export const REQUESTED_UNITTEST_RETRY_REASON =
  "That replacement still was not unittest and was not written. Make another write call now, with no prose, using this exact outer shape: `import subprocess, sys, unittest`; `class Tests(unittest.TestCase):`; one `def test_name(self):` per owner-requested case; each method calls `subprocess.run(...)` and uses `self.assertEqual(...)`; finish with `if __name__ == '__main__': unittest.main()`. Keep the complete file under 1000 characters. If this shape is still invalid, one final literal scaffold remains before the turn stops.";

export const REQUESTED_UNITTEST_FINAL_RETRY_REASON =
  "Your previous replacement repeated the forbidden custom runner and was not written. One last attempt: discard every prior byte. Begin exactly with `import json, subprocess, sys, unittest`, then `class Tests(unittest.TestCase):`. Put each owner-requested case in its own `def test_name(self):`, call `result = subprocess.run([sys.executable, 'PROGRAM.py', 'INPUT'], capture_output=True, text=True)`, and assert its returncode, parsed stdout, or stderr with `self.assertEqual`. Finish with `if __name__ == '__main__': unittest.main()`. Replace PROGRAM.py and INPUT with the requested values. Do not define run_test, all_passed, print, comments, docstrings, or top-level subprocess code. Keep it under 1000 characters. Another invalid shape will stop this turn.";

export const REQUESTED_PARSED_JSON_REQUIRED_REASON =
  "The owner explicitly required parsed JSON verification, so that raw-text comparison test was not written. Write the same test file with `json.loads(result.stdout)` and compare the resulting Python object and numeric values; do not compare JSON whitespace or a literal expression such as `10/3` inside a string.";

export const RECURSIVE_DELETE_REQUIRES_OWNER_REASON =
  "The assistant stopped tool use for this turn because a recursive deletion was not authorized. The deletion was blocked, but earlier actions may have completed. Do not retry through another command, tool, or agent. Explain what was attempted and wait for a new owner instruction.";

export const CANCELLABLE_EXEC_UNAVAILABLE_REASON =
  "The assistant could not establish the exact cancellation boundary for this command. Do not call another tool in this turn; explain that execution is temporarily unavailable.";

export const EXEC_ARGUMENTS_REQUIRE_COMMAND_REASON =
  "The exec command was not a non-empty string, so nothing was executed. Retry with command containing the shell text and workdir as a separate field, not an object inside command. For tool_call, use id exec and args containing those fields. Do not change the intended command or its authority.";

export const WORKSPACE_PREVIEW_REQUIRES_TOOL_REASON =
  "A server started inside the assistant's disposable sandbox is not reachable from the owner's browser. Do not start python http.server, npm dev, Vite, or another background server and do not claim any localhost port. Finish the static files, then call pixel_ods_workspace_preview with their one workspace-relative directory; share only its independently verified URL.";

export const WORKSPACE_PREVIEW_REQUIRES_FILES_REASON =
  "The assistant cannot publish this website yet because this response has not created or inspected an index.html in the requested workspace directory. Create the static site files first, then call pixel_ods_workspace_preview with that one relative directory.";

export const WORKSPACE_PREVIEW_REQUIRES_READBACK_REASON =
  "The host verified the published snapshot. The owner also requested file inspection; complete the remaining file reads alongside any other requested checks. Static publication does not prove functional behavior.";

export const WORKSPACE_PREVIEW_COMPLETE_REASON =
  "The host verified the published snapshot and requested file readbacks are complete. Complete any remaining owner-requested checks, then give the concise final result. Static publication does not prove functional behavior; workspace changes require a fresh publication.";

export const WORKSPACE_VISUAL_CONTINUATION_REQUIRES_READ_REASON =
  "The assistant is updating the most recently verified visual artifact in this chat. Read the existing file inside that exact artifact directory before editing or replacing it; do not guess its contents or create a replacement project.";

export const WORKSPACE_VISUAL_CONTINUATION_REQUIRES_EDIT_REASON =
  "The assistant has not yet completed the requested visual change. Edit or replace a file already read inside the bound artifact directory, then republish that same directory; do not publish an unchanged snapshot.";

export const WORKSPACE_VISUAL_CONTINUATION_SCOPE_REASON =
  "Keep this visual follow-up in the bound artifact directory. Read existing files before editing or replacing them, use sandbox exec/process for inspection and verification, then republish the same directory with pixel_ods_workspace_preview. Do not blindly overwrite files, create a replacement project, or modify another directory.";


export const WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX =
  "The assistant preserved the website files in its workspace, but ODS did not verify a browser-accessible preview. No localhost URL is live or claimed; ask the assistant to continue and publish the static site through the workspace preview capability.";

export const WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX =
  "The assistant did not create or verify the requested website files, so ODS did not publish a browser preview. No localhost URL is live or claimed; ask the assistant to retry the build.";

export const WORKSPACE_PREVIEW_PUBLISHED_DELIVERY_PREFIX =
  "Your preview is ready.";

export const CLIENT_CANCELLED_REASON =
  "The owner cancelled this assistant response. Do not call another tool or continue the task in this turn.";

export const EXACT_DOWNLOAD_REQUIRES_BROKER_REASON =
  "The assistant cannot turn web_fetch or another transformed page view into an exact-byte download. Call pixel_ops_download_stage now; ODS will bind it to the owner's exact HTTPS URL, destination basename, and expected digest. Wait for that exact job with pixel_ops_job_wait, then publish only its verified receipt with pixel_ods_download_promote. Do not create a substitute file.";

export const EXACT_DOWNLOAD_REQUIRES_WAIT_REASON =
  "The assistant submitted the exact-byte staged download but has not obtained its terminal receipt. Call pixel_ops_job_wait now; ODS will bind it to the submitted job. Do not read, recreate, or transfer the quarantine path.";

export const EXACT_DOWNLOAD_REQUIRES_PROMOTION_REASON =
  "The assistant verified the staged artifact in quarantine. Call pixel_ods_download_promote now; ODS will bind the job, source URL, digest, filename, and workspace-relative destination. Do not read the root-only quarantine path or create a substitute file.";

export const EXACT_DOWNLOAD_REQUEST_UNBOUND_REASON =
  "The assistant could not bind this exact-byte request to one unambiguous HTTPS source URL and one safe workspace-relative destination. Do not call another tool or create a substitute; ask the owner for one exact HTTPS URL and destination path.";

export const EXACT_DOWNLOAD_LOOP_ABORT_REASON =
  "The assistant stopped this response because it requested another tool after the exact-download provenance boundary was enforced. Start a fresh message with an approved staged-download capability or ask for a non-byte-exact page summary.";

export const EXACT_DOWNLOAD_UNAVAILABLE_DELIVERY_PREFIX =
  "The assistant did not submit the requested exact-byte download through a verified broker path. No downloadable artifact was created. web_fetch and page extraction return transformed, safety-marked evidence rather than origin bytes; retry with the policy-approved staged-download capability or provide a trusted local artifact and digest.";

export const EXACT_DOWNLOAD_UNVERIFIED_DELIVERY_PREFIX =
  "The assistant did not verify that the requested artifact was staged. A broker request may have been submitted, but exact-byte success requires a matching terminal succeeded Operations receipt with an absolute quarantine path, byte count, SHA-256 digest, HTTPS source, and non-executable artifact evidence. Continue or retry the broker job; do not treat a workspace substitute as the download.";

export const EXACT_DOWNLOAD_UNPUBLISHED_DELIVERY_PREFIX =
  "The assistant verified the requested bytes in Operations quarantine but did not publish them into the owner workspace. No workspace download was accepted; retry the verified create-only promotion path.";

export const EXACT_DOWNLOAD_PROMOTION_FAILED_DELIVERY_PREFIX =
  "The assistant could not publish the verified staged bytes into the owner workspace. No overwrite or substitute file was accepted.";

export const EXACT_DOWNLOAD_PUBLISHED_DELIVERY_PREFIX =
  "ODS securely published the requested exact-byte download into the owner workspace:";

export const EXACT_DOWNLOAD_FAILED_DELIVERY_PREFIX =
  "The staged-download job reached a verified terminal failure. No artifact was created, and the assistant did not claim success.";

export const EXACT_DOWNLOAD_APPROVAL_DELIVERY_PREFIX =
  "ODS staged the requested download as an immutable plan, but external approval is required. No artifact was created, and the assistant did not self-approve it.";

export const OPERATIONS_REQUIRES_BROKER_REASON =
  "The owner requested host or Operations evidence. Generic exec runs inside the assistant's sandbox and cannot establish host facts. For requested host.* observations, use the visible tool_call Tool Search control once with id pixel_ods_host_observe and args containing the exact requested actions; it returns the terminal broker receipt. Use pixel_ops_inventory, pixel_ops_run, and pixel_ops_job_wait only for other named Operations work. A status projection cannot substitute for required host work; use it only for an owner-requested ODS runtime facet after terminal host evidence.";

export const OPERATIONS_NOT_REQUESTED_REASON =
  "The assistant blocked this Operations tool because the owner's current request did not ask for host or ODS Operations work. Continue only the owner's original authorized task. For requested sandbox workspace work, use read, write, edit, apply_patch, exec, or process; do not submit an Operations job or broaden the task.";

export const UNREQUESTED_OPERATIONS_TERMINAL_REASON =
  "The assistant blocked another unrequested Operations attempt after a routing correction. Do not call another tool in this response or submit an Operations job. Give the owner a final answer explaining what was verified and what remains incomplete; existing work is preserved.";

export const UNREQUESTED_OPERATIONS_LOOP_ABORT_REASON =
  "The assistant stopped this response after another tool was requested following repeated unrequested Operations attempts and a terminal no-more-tools instruction. No additional tool was authorized; existing work is preserved.";

export const NETWORK_DISCOVERY_UNVERIFIED_TEXT =
  "LAN discovery and remote SSH availability were not verified by these local-host observations. No peer scan or remote login was performed.";

export const OPERATIONS_INVENTORY_REQUIRES_TOOL_REASON =
  "The owner asked what Operations capabilities are actually available. Call only pixel_ops_inventory with no arguments, then report its bounded current inventory. Do not submit a job, call status, search for tools, or exercise any capability.";

export const OPERATIONS_INVENTORY_COMPLETE_REASON =
  "The assistant already obtained the current bounded Operations capability inventory. Do not call another tool; report that inventory and its authority boundary now.";

export const OPERATIONS_INVENTORY_EVIDENCE_PREFIX =
  "ODS verified the current Operations capability inventory through the external broker's bounded projection:";

export const OPERATIONS_INVENTORY_UNVERIFIED_DELIVERY_PREFIX =
  "The assistant did not obtain a structurally valid current Operations capability inventory. No capability availability or authority claim was accepted.";

export const OPERATIONS_HOST_COMMAND_REQUIRES_PROPOSAL_REASON =
  "The owner requested one protected command from the local ODS host, possibly including an explicit SSH operation to an owner-named destination. Call only pixel_ods_host_command_propose with the exact command. The ODS adapter fixes execution to ods-host and waits internally for the immutable approval plan or terminal broker receipt. Do not use generic exec, inventory, a named action, a workflow, another broker target, pixel_ops_shell_propose, pixel_ops_job_wait, or a second command proposal.";

export const OPERATIONS_HOST_COMMAND_COMPLETE_REASON =
  "The assistant already obtained the broker's terminal state for this protected host-command proposal. Do not call another tool; report the verified approval requirement or terminal outcome now.";

export const OPERATIONS_HOST_COMMAND_EVIDENCE_PREFIX =
  "ODS verified this owner-approved host command through a structurally matched terminal Operations Broker receipt:";

export const WORKSPACE_TOOL_SEARCH_COMPLETE_REASON =
  "The assistant already resolved the deferred workspace tools. Do not search again. Call tool_call now with the returned exact id, such as openclaw:core:exec, openclaw:core:write, openclaw:core:read, openclaw:core:edit, openclaw:core:apply_patch, or openclaw:core:process, and put that tool's normal arguments in args.";

export const WORKSPACE_UNREQUESTED_PROJECTION_REASON =
  "This is a sandbox workspace task, not an ODS status or application-list request. Do not call pixel_ods_status or pixel_ods_apps_list. Call tool_search once for write read edit apply_patch exec process, then use the returned exact workspace tool id to inspect or change only the owner-requested workspace path.";

export const OPERATIONS_REQUIRES_PROJECTIONS_REASON =
  "The assistant completed the requested host Operations jobs, but the owner also requested ODS status evidence that is still missing. Call each requested pixel_ods_status or pixel_ods_apps_list projection exactly once now. After every requested projection is verified, continue any explicitly requested workspace work.";

export const OPERATIONS_LOOP_ABORT_REASON =
  "The assistant stopped this response because it requested another non-Operations tool after the host Operations boundary was enforced. Start a fresh message to retry the named broker action.";

export const OPERATIONS_UNAVAILABLE_DELIVERY_PREFIX =
  "The assistant did not submit the requested host or Operations work through the isolated Operations Broker. No sandbox command was accepted as host evidence.";
export const OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE =
  "operations-unavailable-zero-submissions";

export const OPERATIONS_UNVERIFIED_DELIVERY_PREFIX =
  "The assistant submitted Operations work but did not obtain a matching terminal broker result in this response. Treat the host outcome as pending or unverified, not completed.";

export const OPERATIONS_MISSING_REQUIRED_DELIVERY_PREFIX =
  "The assistant completed its submitted Operations work but did not request every required host observation.";

export const OPERATIONS_WRONG_ACTION_REASON =
  "The assistant blocked an Operations submission that did not match the host facts requested. Use only the exact named ods-host actions listed in this correction, then wait for every submitted job to reach a terminal state.";

export const OPERATIONS_REQUIRES_WORKFLOW_REASON =
  "The assistant blocked a fragmented host inventory. Submit exactly one pixel_ops_workflow_submit containing every required ods-host action, then call pixel_ops_job_wait once for that workflow job. Do not submit separate pixel_ops_run jobs.";

export const OPERATIONS_EXTENSION_LIFECYCLE_SEQUENCE_REASON =
  "The assistant blocked an extension lifecycle shortcut. Submit exactly one ods.extensions.inspect action for the owner's extension ID and wait for its terminal receipt before submitting the requested lifecycle action. Do not combine lifecycle actions in a workflow or continue when inspection reports missing configuration.";

export const OPERATIONS_CONTINUATION_REQUIRES_STATUS_REASON =
  "The assistant blocked a new action while checking an existing immutable Operations plan. Query only the exact owner-supplied job with pixel_ops_job_get or pixel_ops_job_wait; do not resubmit, repeat, approve, or widen the operation.";

export const OPERATIONS_CONTINUATION_COMPLETE_REASON =
  "The assistant already obtained a structurally matched terminal receipt for the exact owner-supplied Operations job and plan hash. Do not call another tool; report only that verified outcome.";

export const OPERATIONS_CONTINUATION_UNVERIFIED_DELIVERY_PREFIX =
  "The assistant did not obtain a structurally matched terminal Operations receipt for the exact owner-supplied job and plan hash. The owner's approval or success statement was not accepted as host evidence.";

export const OPERATIONS_HOST_EVIDENCE_PREFIX =
  "ODS verified these host facts through structurally matched terminal Operations Broker receipts:";

export const OPERATIONS_ODS_APPS_UNAVAILABLE_TEXT =
  "ODS containers: a current sanitized ODS application projection was not obtained. Host Operations facts above remain verified, but the assistant cannot claim a container inventory from them.";

export const OPERATIONS_ODS_STATUS_UNAVAILABLE_TEXT =
  "ODS runtime status: a current sanitized ODS status projection was not obtained. Host Operations facts above remain verified, but the assistant cannot claim the active model, context, version, or assistant availability from them.";

export const OPERATIONS_TRUSTED_CONTINUATION_PREFIX =
  "[ODS assistant trusted continuation]";

export const OPERATIONS_EXTENSION_CATALOG_EVIDENCE_PREFIX =
  "ODS verified this extension catalog result through a structurally matched terminal Operations Broker receipt:";

export const OPERATIONS_EXTENSION_INVENTORY_EVIDENCE_PREFIX =
  "ODS verified this live extension inventory through a structurally matched terminal Operations Broker receipt:";

export const OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX =
  "ODS verified this extension lifecycle result through structurally matched Operations Broker receipts:";

const WEB_TOOLS = new Set(["web_search", "web_fetch", "pixel_ods_web_extract", "pixel_ods_research"]);
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
  "The verification command failed. Preserve the owner's explicit behavior contract: correct a test only when its expectation contradicts the owner; otherwise repair the implementation, and never weaken an assertion merely to match broken output. A blank label such as `Invalid integer:` is not a helpful empty-input message. When the failure already contains actual and expected evidence, apply one focused edit to the file implicated by the failure (test or implementation), then rerun the same verification command. If the failure is a missing-file error for a file you previously wrote, recreate it before rerunning. If evidence is insufficient, read the relevant file or run a focused diagnostic, then repair and rerun verification. Report an unresolved blocker honestly when the available tools cannot resolve it.";
const EXACT_DOWNLOAD_BROKER_TOOLS = new Set([
  "pixel_ops_download_stage",
  "pixel_ops_job_get",
  "pixel_ops_job_wait",
  "pixel_ods_download_promote",
]);
const DOWNLOAD_JOB_TOOLS = new Set([
  "pixel_ops_job_get", "pixel_ops_job_wait", "pixel_ops_job_events", "pixel_ops_job_cancel",
]);
const OPERATIONS_TOOLS = new Set([
  "pixel_ods_extensions",
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
const EXTENSION_READ_TOOL = "pixel_ods_extensions";
const SYNCHRONOUS_HOST_COMMAND_TOOL = "pixel_ods_host_command_propose";
const EVIDENCE_REPORT_TOOL = "pixel_ods_evidence_report";
const EVIDENCE_READBACK_TOOL = "pixel_ods_evidence_readback";
const WORKSPACE_PREVIEW_TOOL = "pixel_ods_workspace_preview";
const MAX_TRACKED_RUNS = 256;
const MAX_SESSION_DOWNLOAD_JOBS = 32;
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
  executionHost = "sandbox",
} = {}) {
  if (executionHost !== "sandbox" && executionHost !== "gateway") {
    throw new Error("invalid Pixel execution control host mode");
  }
  const resolvedRoot = path.resolve(root);
  const hostWrapper = path.join(resolvedRoot, "cancellable-exec.sh");

  function assertRoot() {
    const owner = typeof process.getuid === "function" ? process.getuid() : undefined;
    const rootInfo = fs.lstatSync(resolvedRoot);
    const wrapperInfo = fs.lstatSync(hostWrapper);
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
      // Validate the owner-side file above even when execution uses its sandbox
      // bind mount. Gateway execution uses that same verified file directly.
      const wrapper = executionHost === "sandbox"
        ? EXEC_CONTROL_WRAPPER
        : `'${hostWrapper.replace(/'/g, "'\"'\"'")}'`;
      return `${wrapper} ${execMarkerId(runId)} ${encoded}`;
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
  if (typeof value !== "string") return value;
  // These spellings name the same sandbox path. Do not resolve '..' or
  // arbitrary absolute paths: scope validation must still reject them.
  value = value.replace(/^(?:\.\/)+/, "");
  if (value === "/workspace" || value === "workspace") return ".";
  if (value.startsWith("/workspace/")) {
    value = value.slice("/workspace/".length);
  } else if (value.startsWith("workspace/")) {
    value = value.slice("workspace/".length);
  }
  return value.replace(/^(?:\.\/)+/, "");
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
  // A model may wrap the Tool Search transport in itself. Resolve only one
  // exact redundant envelope around a known workspace tool, before the missing
  // tool fuse can disable even corrected calls for the remainder of the turn.
  // The resolved call still passes every ordinary workspace/host/cancel guard.
  if (
    toolName === "tool_call" && params.id === "tool_call" &&
    Object.keys(params).length === 2 &&
    params.args && typeof params.args === "object" && !Array.isArray(params.args) &&
    Object.keys(params.args).length === 2 &&
    typeof params.args.id === "string" &&
    /^(?:openclaw:core:)?(?:read|write|edit|apply_patch|exec|process)$/.test(params.args.id) &&
    params.args.args && typeof params.args.args === "object" && !Array.isArray(params.args.args)
  ) {
    return normalizeWorkspaceParams(toolName, params.args) ?? { ...params.args };
  }
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
  // A model can emit a tool_call with the workdir field at the outer
  // envelope level instead of inside args for exec. When the outer shape
  // is exactly { id, args, workdir } and args has no workdir, normalize
  // the outer workdir into args. Accept relative workspace paths and
  // absolute /workspace/... paths; reject everything else.
  const outerWorkdir = params.workdir;
  if (
    toolName === "tool_call" &&
    nestedCoreToolName === "exec" &&
    Object.keys(params).length === 3 &&
    Object.hasOwn(params, "id") &&
    Object.hasOwn(params, "args") &&
    Object.hasOwn(params, "workdir") &&
    params.args !== null &&
    typeof params.args === "object" &&
    !Array.isArray(params.args) &&
    !Object.hasOwn(params.args, "workdir") &&
    typeof outerWorkdir === "string" &&
    outerWorkdir.length > 0 &&
    outerWorkdir.length <= 256 &&
    ((/^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/.test(outerWorkdir) &&
     !outerWorkdir.split("/").includes("..")) ||
     (/^\/workspace\/[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/.test(outerWorkdir) &&
      !outerWorkdir.split("/").includes("..")))
  ) {
    updated.args = { ...updated.args, workdir: normalizeExecWorkdir(outerWorkdir) };
    delete updated.workdir;
    changed = true;
  }
  // The 9B workspace agent repeatedly emitted {code: "ls -la tidy-demo/"}
  // after choosing exec. Adapt that exact shell-tool envelope before retry
  // accounting and cancellation. Interpreter, host, or conflicting alias
  // fields remain invalid; do not let a later alias branch pick a winner.
  if (toolName === "exec" && Object.hasOwn(params, "code") && typeof params.command !== "string") {
    if (
      params.command !== undefined ||
      typeof params.code !== "string" ||
      (params.context !== undefined && params.context !== "fork") ||
      !Object.keys(params).every((key) =>
        ["code", "context", "workdir", "yieldMs", "timeout", "pty", "background"].includes(key)
      )
    ) return undefined;
    updated.command = params.code;
    delete updated.code;
    if (updated.context === "fork") delete updated.context;
    changed = true;
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
  // Native file tasks sometimes use filePath instead of the core path field.
  // Recover an unambiguous alias through the same normalization and checks as
  // canonical input. Leave an existing path and malformed aliases untouched;
  // the core tool host still enforces the filesystem sandbox.
  if (
    FILE_PATH_TOOLS.has(toolName) &&
    Object.hasOwn(params, "filePath") &&
    !Object.hasOwn(params, "path") &&
    typeof params.filePath === "string" &&
    params.filePath !== ""
  ) {
    updated.path = normalizeWorkspaceFilePath(params.filePath);
    delete updated.filePath;
    changed = true;
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
    ["path\u0000text", "overwrite\u0000path\u0000text"].includes(
      Object.keys(updated).sort().join("\u0000")
    ) &&
    (!Object.hasOwn(updated, "overwrite") || updated.overwrite === true) &&
    typeof params.text === "string"
  ) {
    // Core write already replaces the target. Compact models sometimes add
    // overwrite:true to the text alias; it grants no additional capability.
    // Keep overwrite:false and unfamiliar options unmodified, because core
    // write cannot honor their potentially different semantics.
    updated.content = params.text;
    delete updated.text;
    delete updated.overwrite;
    changed = true;
  }
  if (
    toolName === "edit" &&
    typeof updated.path === "string" &&
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

// This recognizes result evidence only; the pre-execution audit/authorization
// gate still uses verificationCommandIsAuditable without accepting new syntax.
function andChainVerificationParams(params) {
  if (typeof params?.command !== "string" || /[\r\n]/.test(params.command)) return undefined;
  const command = params.command.trim().replace(/\s+2>&1\s*$/i, "").trim();
  const segments = command.split("&&").map((segment) => segment.trim());
  if (segments.length < 2) return undefined;
  // Deliberately support only literal simple words. Quotes, escapes, comments,
  // substitutions, redirections and other shell operators require a parser.
  if (segments.some((segment) => !/^[A-Za-z0-9._/*?=,:@%+\-]+(?:[ \t]+[A-Za-z0-9._/*?=,:@%+\-]+)*$/.test(segment))) {
    return undefined;
  }
  let workdir = params.workdir;
  const workspaceCd = segments[0].match(/^cd[ \t]+(\/workspace(?:\/[A-Za-z0-9._/-]+)?)$/);
  if (workspaceCd) {
    workdir = workspaceCd[1];
    segments.shift();
  }
  const finalCommand = segments.pop();
  // Shell builtins such as exit, exec, set, eval, trap and later cd commands
  // can skip/mask the final test or change its cwd despite an exit-zero receipt.
  // Restrict setup to ordinary commands that cannot change the parent shell.
  if (!segments.length || segments.some((segment) => !/^(?:rm|mkdir|cp|mv|touch|echo|true)(?:[ \t]|$)/.test(segment))) {
    return undefined;
  }
  const finalParams = { ...params, command: finalCommand, workdir };
  return verificationCommand(finalParams) && verificationCommandIsAuditable(finalParams)
    ? finalParams : undefined;
}

function verificationExecFingerprint(params) {
  let parsed = verificationCommand(params);
  if (!parsed || !verificationCommandIsAuditable(params)) {
    params = andChainVerificationParams(params);
    if (!params) return undefined;
    parsed = verificationCommand(params);
  }
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

function submittedDownloadJobId(event) {
  if (toolCallFailed(event)) return undefined;
  const details = event?.result?.details;
  return details && typeof details === "object" && !Array.isArray(details) &&
    details.status === "submitted" && details.kind === "download" &&
    typeof details.jobId === "string" && OPS_JOB_ID.test(details.jobId)
    ? details.jobId : undefined;
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
    (params.expectedSha256 != null &&
      (typeof params.expectedSha256 !== "string" || !SHA256.test(params.expectedSha256))) ||
    (params.expectedSha256 ?? undefined) !== (requested?.expectedSha256 ?? undefined) ||
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
    expectedSha256: params.expectedSha256 ?? undefined,
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
    ...(submission.requiredNetworkPeer ? { requiredNetworkPeer: submission.requiredNetworkPeer } : {}),
  };
}

function requiredHostObservationActions(state) {
  if (!state?.operationsRequired) return undefined;
  // Host evidence is one part of a mixed request. Keep every other required
  // Operations action pending while observing only the requested host facts.
  const actions = [...state.operationsRequiredActions].filter((action) => action.startsWith("host."));
  return actions.length > 0 ? actions : undefined;
}

function synchronousHostObservationOutcome(event, state) {
  if (toolCallFailed(event)) return undefined;
  const admitted = permittedHostObservationParams(event?.params, state?.hostObservationPolicy);
  const expectedActions = admitted?.actions;
  const observedActions = event?.params?.actions;
  const expectedPeer = expectedActions?.includes("host.network-peer") ? admitted : undefined;
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
    ...(expectedPeer ? { requiredNetworkPeer: state.operationsNetworkPeer } : {}),
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
  return { submission, outcome };
}

function synchronousExtensionObservation(event) {
  if (toolCallFailed(event)) return undefined;
  const params = event?.params;
  const jobId = event?.result?.details?.jobId;
  if (!params || !["search", "list", "inspect"].includes(params.action) ||
      typeof jobId !== "string" || !OPS_JOB_ID.test(jobId)) return undefined;
  const submission = { jobId, actions: [{
    target: params.target === undefined ? "ods-host" : params.target,
    action: `ods.extensions.${params.action}`,
    parameters: params.action === "search" ? { query: params.query === undefined ? "all" : params.query }
      : params.action === "inspect" ? { serviceId: params.serviceId } : {},
  }] };
  const outcome = operationsTerminalOutcome(
    { params: { jobId }, result: event.result }, new Map([[jobId, submission]])
  );
  // Track a submitted read even when the synchronous wait timed out. Later
  // get/wait calls must bind to this job, rather than resubmitting the action.
  return { submission, outcome };
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
    // ps comm is one trailing field, not necessarily one word. Real process
    // names can contain ordinary spaces. The broker supplies comm, not argv;
    // keep the numeric prefix and bounded alphabet excluding markup/controls.
    const match = line.match(/^([0-9]+)\s+([0-9]+)\s+([A-Za-z0-9_.+-]{1,64})\s+(\S{1,16})\s+([0-9.]+)\s+([0-9.]+)\s+([A-Za-z0-9_.+:@%()\/ -]{1,128})$/);
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
  // A model can split a peer observation across calls. Completion still
  // requires every requested port, with a valid receipt for the same peer.
  const peerRequirement = [...terminalJobs.values()].find((outcome) =>
    outcome.requiredNetworkPeer)?.requiredNetworkPeer;
  const peerSteps = [];
  if (requiredActions.has("host.network-peer") && peerRequirement) {
    const coveredPorts = new Set();
    for (const outcome of terminalJobs.values()) {
      if (outcome.status !== "succeeded") continue;
      for (const step of outcome.steps) {
        if (step.target !== "ods-host" || step.action !== "host.network-peer") continue;
        const parameters = outcome.actions.find((entry) =>
          entry.target === step.target && entry.action === step.action)?.parameters;
        const evidence = { ...step, parameters, jobId: outcome.jobId };
        if (parameters?.peer !== peerRequirement.peer || !networkPeerEvidence(evidence)) continue;
        peerSteps.push(evidence);
        for (const port of parameters.ports.split(",").map(Number)) coveredPorts.add(port);
      }
    }
    if (!peerRequirement.ports.every((port) => coveredPorts.has(port))) steps.delete("host.network-peer");
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
    return `The required host Operations job reached terminal status ${failedRequiredOutcome.status}. Job: ${failedRequiredOutcome.jobId}.${plan}`;
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
    const observations = action === "host.network-peer" && peerSteps.length ? peerSteps : [step];
    for (const observation of observations) {
      const value = renderer(observation);
      if (!value) return undefined;
      lines.push(`- ${label}: ${value} (job \`${observation.jobId}\`)`);
    }
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
      `- ODS runtime projection: ${runtime}; assistant ${availability}; ODS version \`${odsStatusProjection.ods_version}\`.`
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
    ![expectedToolName, `openclaw:${expectedSourceName}:${expectedToolName}`].includes(params.id)
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

// Tool Search catches sandbox read failures outside its usual selected-tool
// envelope. Bind that error to the pre-call read identity before using it.
function toolSearchSandboxMissingRead(event, pending, runId) {
  if (pending?.runId !== runId || pending.selectedToolName !== "read" ||
      !["read", "openclaw:core:read"].includes(event?.params?.id)) return undefined;
  const requestedPath = normalizeWorkspaceFilePath(pending.selectedParams?.path);
  if (typeof requestedPath !== "string" || !requestedPath ||
      normalizeWorkspaceFilePath(event.params?.args?.path) !== requestedPath) return undefined;
  const result = event.result;
  const payload = result?.details?.status === "error" ? result.details : result;
  let error = typeof event.error === "string" ? event.error : undefined;
  if (payload?.status === "error" && payload.tool === "tool_call" &&
      typeof payload.error === "string") error = payload.error;
  if (!error && result?.isError === true && Array.isArray(result.content)) {
    for (const block of result.content) {
      if (block?.type !== "text" || typeof block.text !== "string") continue;
      try {
        const parsed = JSON.parse(block.text);
        if (parsed?.status === "error" && parsed.tool === "tool_call" &&
            typeof parsed.error === "string") error = parsed.error;
      } catch { /* Successful file contents are never missing-file evidence. */ }
    }
  }
  const match = error?.match(/^Sandbox FS error \(ENOENT\): ([^\r\n]+)$/);
  if (!match || normalizeWorkspaceFilePath(match[1]) !== requestedPath) return undefined;
  return {params: pending.selectedParams,
    result: {isError: true, details: {code: "ENOENT", path: requestedPath}}};
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

function extensionCatalogResult(step, submittedAction) {
  const submittedParameters = submittedAction?.parameters;
  if (
    !step ||
    typeof submittedAction?.target !== "string" ||
    step.target !== submittedAction.target ||
    step.action !== "ods.extensions.search" ||
    submittedAction.action !== step.action ||
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
    const scoped = Object.prototype.hasOwnProperty.call(entry ?? {}, "catalogSource");
    if (!exactKeys(entry, scoped ? [...entryKeys, "catalogSource", "configurationScope"] : entryKeys)) return undefined;
    if (scoped && (!["library", "builtin"].includes(entry.catalogSource) ||
        entry.configurationScope !== "declared-environment-keys")) return undefined;
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
      catalogSource: entry.catalogSource ?? "library",
      configurationScope: "declared-environment-keys",
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
    typeof submittedAction?.target !== "string" ||
    step.target !== submittedAction.target ||
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
  const scopedConfiguration = Object.prototype.hasOwnProperty.call(value ?? {}, "configurationScope");
  if (scopedConfiguration) topKeys.push("configurationScope", "runtimeRequirementsVerified");
  if (
    !exactKeys(value, topKeys) ||
    (scopedConfiguration && (value.configurationScope !== "declared-environment-keys" ||
      value.runtimeRequirementsVerified !== false)) ||
    value.schemaVersion !== 1 ||
    value.kind !== "ods-pixel-extension-lifecycle" ||
    value.boundary !== EXTENSION_LIFECYCLE_BOUNDARY ||
    value.action !== expectedAction ||
    value.extensionId !== submittedParameters.serviceId ||
    !["ready", "inspected", "blocked", "noop", "succeeded", "failed"].includes(value.outcome) ||
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
      !["ready", "inspected", "blocked", "failed"].includes(value.outcome) ||
      value.currentStatus !== value.previousStatus ||
      value.changed ||
      value.externalEffectOccurred ||
      value.rollback.attempted ||
      (value.outcome !== "failed" && ["ready", "inspected"].includes(value.outcome) !== (missing.length === 0))
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
    return `ODS rechecked Operations job ${outcome.jobId} with plan SHA-256 ${outcome.planHash}; the host still reports awaiting-approval. No operation was accepted as completed.`;
  }
  if (outcome.status !== "succeeded") {
    return `ODS rechecked Operations job ${outcome.jobId} with plan SHA-256 ${outcome.planHash}; its verified terminal status is ${outcome.status}. No successful operation result was accepted.`;
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
  return ["ready", "inspected"].includes(inspection?.result?.outcome) &&
    EXTENSION_LIFECYCLE_SUCCESS.get(action)?.has(inspection.result.currentStatus) === true;
}

const EXTENSION_READ_ACTIONS = new Set([
  "ods.extensions.search", "ods.extensions.list", "ods.extensions.inspect",
]);

function extensionDiscoveryEligible(state) {
  return state && !state.operationsHostCommandRequested &&
    !state.operationsExpectedExtensionLifecycle && !state.operationsContinuation &&
    !state.exactDownloadRequested &&
    [...state.operationsRequiredActions].every((action) => EXTENSION_READ_ACTIONS.has(action));
}

function extensionDiscoveryActive(state) {
  return extensionDiscoveryEligible(state) &&
    (state.extensionDiscoveryUsed || state.operationsRequiredActions.size > 0);
}

function extensionReadSubmission(toolName, params) {
  const steps = toolName === "pixel_ops_run" ? [params]
    : toolName === "pixel_ops_workflow_submit" ? params?.steps : undefined;
  return Array.isArray(steps) && steps.length > 0 && steps.every((step) =>
    step && typeof step === "object" && !Array.isArray(step) &&
    typeof step.target === "string" && EXTENSION_READ_ACTIONS.has(step.action));
}

function extensionInspectionEvidence(step, action, jobId) {
  const result = extensionLifecycleResult(step, action);
  if (!result || result.action !== "inspect") return undefined;
  return [
    OPERATIONS_EXTENSION_LIFECYCLE_EVIDENCE_PREFIX,
    `- Target: \`${action.target}\`; extension: \`${result.extensionId}\`.`,
    `- Inspection: \`${result.outcome}\`; current state: \`${result.currentStatus}\`.`,
    result.outcome === "failed"
      ? "- Required configuration could not be established by this failed inspection."
      : `- Missing required configuration keys: ${result.missingConfiguration.length ? result.missingConfiguration.map((key) => `\`${key}\``).join(", ") : "none"}.`,
    "- Configuration scope: declared environment keys only. Runtime prerequisites and features have not been verified by this inspection.",
    "- This inspection made no change and grants no installation or configuration authority.",
    `- Broker job: \`${jobId}\`.`,
  ].join("\n");
}

function extensionDiscoveryVerification(state) {
  if (state.operationsSubmittedJobs.size === 0) {
    return { status: "failed", text: OPERATIONS_UNAVAILABLE_DELIVERY_PREFIX,
      ...(!state.toolExecutionAttempted ? { code: OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE } : {}) };
  }
  const evidence = [];
  const hostJobs = new Map();
  let successes = 0;
  for (const [jobId, submission] of state.operationsSubmittedJobs) {
    const outcome = state.operationsTerminalJobs.get(jobId);
    const hostObservation = submission.actions.length > 0 && submission.actions.every(({ target, action }) =>
      target === "ods-host" && (HOST_OBSERVATION_FACETS.has(action) || action === "host.network-peer"));
    if (!outcome || (!hostObservation &&
        !submission.actions.every(({ action }) => EXTENSION_READ_ACTIONS.has(action)))) {
      return { status: "failed", text: OPERATIONS_UNVERIFIED_DELIVERY_PREFIX };
    }
    if (outcome.status !== "succeeded") {
      const targets = submission.actions.map(({ target, action }) =>
        `${JSON.stringify(target)} / ${JSON.stringify(action)}`).join(", ");
      evidence.push(`- Broker job \`${jobId}\`: \`${outcome.status}\`; requested ${targets}. No successful result was accepted for this attempt.`);
      continue;
    }
    // Extension diagnosis may legitimately collect local machine facts. Those
    // separately validated observations do not turn completed extension jobs
    // back into pending jobs, or substitute for an extension result.
    if (hostObservation) {
      hostJobs.set(jobId, outcome);
      continue;
    }
    // Match each result to its submitted parameters as well as target/action.
    // A workflow may search several different queries or inspect several IDs;
    // consuming matched steps avoids rebinding one receipt to another action.
    const remaining = [...outcome.steps];
    for (const action of submission.actions) {
      let text;
      const index = remaining.findIndex((step) => {
        if (step.target !== action.target || step.action !== action.action) return false;
        text = action.action === "ods.extensions.inspect"
          ? extensionInspectionEvidence(step, action, jobId)
          : operationsEvidenceText(new Set([action.action]), new Map([[jobId, {
            ...outcome, actions: [action], steps: [step],
          }]]));
        return typeof text === "string";
      });
      if (index < 0) return { status: "failed", text: OPERATIONS_UNVERIFIED_DELIVERY_PREFIX };
      remaining.splice(index, 1);
      evidence.push(text);
      successes += 1;
    }
  }
  if (hostJobs.size > 0) {
    const actions = new Set([...hostJobs.values()].flatMap((outcome) =>
      outcome.actions.map(({ action }) => action)));
    const text = operationsHostEvidenceText(actions, hostJobs);
    if (!text?.startsWith(OPERATIONS_HOST_EVIDENCE_PREFIX)) {
      return { status: "failed", text: OPERATIONS_UNVERIFIED_DELIVERY_PREFIX };
    }
    evidence.push(text);
  }
  return { status: successes > 0 ? "passed" : "failed", text: evidence.join("\n\n") };
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
    return `The ODS extension inspection job reached terminal status ${inspectionOutcome.status}. No lifecycle change was accepted. Job: ${inspectionOutcome.jobId}.${plan}`;
  }
  const inspection = parsedLifecycleOutcome(terminalJobs, "ods.extensions.inspect");
  if (!inspection || !["ready", "inspected", "blocked"].includes(inspection.result.outcome)) return undefined;
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
    return `ODS prepared the exact ${mutationAction} plan for extension ${inspection.result.extensionId}, but external approval is required. No lifecycle change was executed. Job: ${mutationOutcome.jobId}. Plan SHA-256: ${mutationOutcome.planHash}.`;
  }
  if (mutationOutcome.status !== "succeeded") {
    return `The ODS extension lifecycle job reached terminal status ${mutationOutcome.status}. No successful lifecycle result was accepted. Job: ${mutationOutcome.jobId}.`;
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
        ? `ODS prepared a protected ODS host command plan, but external approval is required. No command was executed. Job: ${outcome.jobId}. Plan SHA-256: ${outcome.planHash}.`
        : undefined;
    }
    if (outcome.status !== "succeeded") {
      const plan = typeof outcome.planHash === "string" && SHA256.test(outcome.planHash)
        ? ` Plan SHA-256: ${outcome.planHash}.`
        : "";
      return `The protected ODS host command job reached terminal status ${outcome.status}. No successful command result was accepted. Job: ${outcome.jobId}.${plan}`;
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
      return `The live ODS extension inventory job reached terminal status ${outcome.status}. No extension-state result or external effect was accepted. Job: ${outcome.jobId}.${plan}`;
    }
    if (outcome.steps.length !== 1 || outcome.actions.length !== 1) return undefined;
    const result = extensionInventoryResult(outcome.steps[0], outcome.actions[0]);
    if (!result) return undefined;
    const lines = [
      OPERATIONS_EXTENSION_INVENTORY_EVIDENCE_PREFIX,
      `- Target: \`${outcome.actions[0].target}\`.`,
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
        `- ODS runtime projection: ${runtime}; assistant ${availability}; ODS version \`${odsStatusProjection.ods_version}\`; ${odsStatusProjection.online_app_count} of ${odsStatusProjection.app_count} projected containers online.`
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
    return `The ODS extension catalog job reached terminal status ${outcome.status}. No catalog result or external effect was accepted. Job: ${outcome.jobId}.${plan}`;
  }
  if (outcome.steps.length !== 1 || outcome.actions.length !== 1) return undefined;
  const result = extensionCatalogResult(
    outcome.steps[0],
    outcome.actions[0]
  );
  if (!result) return undefined;
  const lines = [
    OPERATIONS_EXTENSION_CATALOG_EVIDENCE_PREFIX,
    `- Target: \`${outcome.actions[0].target}\`.`,
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
      lines.push(`  - Catalog source: \`${match.catalogSource}\`.`);
      lines.push(`  - Dependencies: ${compactList(match.dependsOn)}.`);
      lines.push(`  - Required configuration keys: ${compactList(match.requiredConfiguration)}.`);
      lines.push(`  - Optional configuration keys: ${compactList(match.optionalConfiguration)}.`);
    }
  } else {
    lines.push("- Matches: none.");
  }
  lines.push("- Installed/enabled state: not included in this read-only catalog receipt; the assistant will inspect one exact extension ID before any lifecycle action.");
  lines.push("- Configuration scope: declared environment keys only, not exhaustive runtime prerequisites. No match establishes only absence from this catalog snapshot.");
  lines.push("- Authority: read-only catalog projection; no installation or configuration authority.");
  lines.push(`- Broker job: \`${outcome.jobId}\`.`);
  return lines.join("\n");
}

function mixedHostInventoryVerification(state) {
  const required = state.operationsRequiredActions;
  const host = new Set(requiredHostObservationActions(state) ?? []);
  if (!required.has("ods.extensions.list") || host.size === 0 || required.size !== host.size + 1) {
    return undefined;
  }
  const jobs = state.operationsTerminalJobs;
  // These are already receipt-validated terminal outcomes. Preserve each
  // original job's status and ID when selecting its requested evidence.
  if ([...jobs.values()].some((job) => job.actions.some(({ target, action }) =>
    target !== "ods-host" || !required.has(action)
  ))) return { status: "failed", text: OPERATIONS_UNVERIFIED_DELIVERY_PREFIX };
  const select = (actions) => new Map([...jobs].flatMap(([id, job]) => {
    const selected = job.actions.filter(({ action }) => actions.has(action));
    return selected.length ? [[id, {
      ...job, actions: selected,
      steps: job.steps.filter(({ action }) => actions.has(action)),
    }]] : [];
  }));
  const hostJobs = select(host);
  const inventoryJobs = select(new Set(["ods.extensions.list"]));
  const hostText = operationsHostEvidenceText(host, hostJobs,
    state.operationsOdsAppsProjection, state.operationsOdsStatusProjection);
  const inventoryText = operationsEvidenceText(new Set(["ods.extensions.list"]), inventoryJobs);
  const lines = [hostText, inventoryText].filter(Boolean);
  if (!hostText) lines.push(`${OPERATIONS_MISSING_REQUIRED_DELIVERY_PREFIX} Missing or unverified: ${[...host].map((action) => `\`${action}\``).join(", ")}.`);
  if (!inventoryText) lines.push(`${OPERATIONS_MISSING_REQUIRED_DELIVERY_PREFIX} Missing or unverified: \`ods.extensions.list\`.`);
  const projectionsReady =
    (!state.operationsRequiresOdsAppsProjection || state.operationsOdsAppsProjection) &&
    (!state.operationsRequiresOdsStatusProjection || state.operationsOdsStatusProjection);
  if (state.operationsRequiresOdsAppsProjection && !state.operationsOdsAppsProjection) lines.push(OPERATIONS_ODS_APPS_UNAVAILABLE_TEXT);
  if (state.operationsRequiresOdsStatusProjection && !state.operationsOdsStatusProjection) lines.push(OPERATIONS_ODS_STATUS_UNAVAILABLE_TEXT);
  if (state.operationsNetworkDiscoveryRequested) lines.push(NETWORK_DISCOVERY_UNVERIFIED_TEXT);
  return {
    status: hostText?.startsWith(OPERATIONS_HOST_EVIDENCE_PREFIX) &&
      inventoryText?.startsWith(OPERATIONS_EXTENSION_INVENTORY_EVIDENCE_PREFIX) &&
      projectionsReady && !state.operationsNetworkDiscoveryRequested &&
      !state.operationsWorkspaceContinuationRequested ? "passed" : "failed",
    text: lines.join("\n"),
  };
}

export function canonicalGitHubSourceMatches(source, repository) {
  try {
    const expected = new URL(repository);
    const actual = new URL(source);
    if (expected.protocol !== "https:" || expected.hostname !== "github.com" ||
        expected.username || expected.password || expected.port ||
        actual.protocol !== "https:" || actual.username || actual.password || actual.port) return false;
    const repo = expected.pathname.split("/").filter(Boolean);
    if (repo.length !== 2 || repo.some((part) => !/^[A-Za-z0-9_.-]+$/.test(part))) return false;
    let parts = actual.pathname.split("/").filter(Boolean);
    if (actual.hostname === "api.github.com") {
      if (parts[0] !== "repos") return false;
      parts = parts.slice(1);
    } else if (!["github.com", "raw.githubusercontent.com"].includes(actual.hostname)) return false;
    return parts.length >= 2 && parts.slice(0, 2).every((part, index) =>
      part.toLowerCase() === repo[index].toLowerCase());
  } catch { return false; }
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
  const deliveryContractIndex = Math.max(
    currentText.lastIndexOf("\n\n[ODS assistant delivery requirement:"),
    currentText.lastIndexOf("\n\n[ODS Pixel delivery requirement:")
  );
  return deliveryContractIndex >= 0
    ? currentText.slice(0, deliveryContractIndex)
    : currentText;
}

function explicitlyRejectsOdsTool(text, toolPattern) {
  const actionNegation = new RegExp(
    `\\b(?:do\\s+not|don't|never|must\\s+not|should\\s+not)\\s+` +
      `(?:call|invoke|query|run|use|inspect|check|observe|report|list)\\b[^.!?;\\n]{0,80}\\b(?:${toolPattern})\\b`,
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

const HOST_OBSERVATION_FACETS = new Map([
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

// This is a capability boundary, separate from the completion checklist.
// Local typed reads need no positive prompt classifier. Explicit exclusions
// still apply, and contacting a peer remains bound to the owner's named target.
function hostObservationPolicy(messages, prompt) {
  const text = withoutFilesystemPaths(currentOwnerIntentText(messages, prompt));
  const excluded = (pattern) => explicitlyExcludesHostObservation(text, pattern) ||
    new RegExp(`\\bno\\s+(?:${pattern})(?:\\s+(?:inspection|observations?|checks?|access|probing))?\\b`, "i").test(text);
  const hostDenied = excluded("(?:ODS\\s+)?host(?:\\s+health)?|host_observe|this\\s+(?:computer|machine)|system\\s+inspection");
  const networkDenied = excluded("network(?:\\s+(?:location|details?))?|LAN|interfaces?|addresses?|ip addresses?");
  return {
    allowedActions: new Set([...HOST_OBSERVATION_FACETS].filter(([action, facet]) =>
      Boolean(text) && !hostDenied &&
      (action === "host.network-peer" || !excluded(facet)) &&
      !(networkDenied && ["host.network-addresses", "host.network-routes", "host.listening-ports", "host.tailscale", "host.network-peer"].includes(action))
    ).map(([action]) => action)),
    peer: userMessageNetworkPeerRequest(messages, prompt),
    allowOdsApps: !explicitlyRejectsOdsTool(text, "ODS\\s+apps?|app\\s+(?:status|inventory)|pixel_ods_apps_list") &&
      !explicitlyExcludesHostObservation(text, "ODS\\s+apps?|app\\s+(?:status|inventory)"),
    allowOdsStatus: !explicitlyRejectsOdsTool(text, "ODS\\s+status|pixel_ods_status") &&
      !explicitlyExcludesHostObservation(text, "ODS\\s+status"),
  };
}

function permittedHostObservationParams(params, policy) {
  if (!policy || !params || typeof params !== "object" || Array.isArray(params) ||
      Object.keys(params).some((key) => !["actions", "peer", "ports", "includeOdsStatus"].includes(key)) ||
      !Array.isArray(params.actions) || params.actions.length < 1 ||
      new Set(params.actions).size !== params.actions.length ||
      params.actions.some((action) => !policy.allowedActions.has(action)) ||
      (params.includeOdsStatus !== undefined && typeof params.includeOdsStatus !== "boolean") ||
      (params.includeOdsStatus === true && !policy.allowOdsStatus)) return undefined;
  if (!params.actions.includes("host.network-peer")) {
    // Do not silently discard a target attached to a local-only observation.
    if (params.peer !== undefined || params.ports !== undefined) return undefined;
    return { ...params, actions: [...params.actions] };
  }
  const peer = policy.peer;
  const normalize = (value) => typeof value === "string" ? value.trim().replace(/\.$/, "").toLowerCase() : undefined;
  const ports = params.ports ?? peer?.ports;
  if (!peer || normalize(params.peer) !== normalize(peer.peer) ||
      !Array.isArray(ports) || !ports.length || ports.length > 8 ||
      new Set(ports).size !== ports.length || ports.some((port) => !peer.ports.includes(port))) return undefined;
  return { ...params, actions: [...params.actions], peer: peer.peer, ports: [...ports] };
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

// Keep status UI elements separate from requests for platform facts.
function statusKeywordIsUiNounPhrase(clause, keywordIndex, keywordLen) {
  const uiWords =
    /\b(?:preview|component|page|dashboard|message|indicator|display|design|monitor|light|accessible|contrast)\b/i;
  const prepBoundary =
    /\b(?:in|on|at|by|for|with|from|to|of|about|into|through|during|shown|displayed|using|via|where|which|that|and|but|or)\b/i;
  const punctBoundary = /[,.?;:!]/;

  const words = [];
  const wordRe = /\S+/g;
  let m;
  while ((m = wordRe.exec(clause)) !== null) {
    words.push({ text: m[0], start: m.index, end: m.index + m[0].length });
  }

  let kwWordIdx = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].start <= keywordIndex && words[i].end >= keywordIndex + keywordLen) {
      kwWordIdx = i;
      break;
    }
  }
  if (kwWordIdx === -1) return false;

  for (let i = kwWordIdx - 1; i >= Math.max(0, kwWordIdx - 4); i--) {
    if (uiWords.test(words[i].text)) return true;
    if (prepBoundary.test(words[i].text) || punctBoundary.test(words[i].text)) break;
  }
  for (let i = kwWordIdx + 1; i < Math.min(words.length, kwWordIdx + 5); i++) {
    if (uiWords.test(words[i].text)) return true;
    if (prepBoundary.test(words[i].text) || punctBoundary.test(words[i].text)) break;
  }
  return false;
}

export function userMessageOdsToolRequirements(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
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
    .split(/[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)|\b(?:although|though|because|since|whereas|while|given\s+that)\b/i)
    .some((clause) => {
      const modelState = clause.match(
        /\b(?:(?:active|current|loaded|running)\s+(?:ODS\s+|Pixel\s+)?model|(?:ODS\s+|Pixel\s+)?model\s+(?:is\s+)?(?:currently\s+)?(?:active|current|loaded|running))\b/i
      );
      if (!modelState) return false;
      // A query must address the model fact. Verbs describing what an already
      // identified model can do must not become prerequisites for other work.
      const beforeModel = clause.slice(0, modelState.index);
      const afterModel = clause.slice(modelState.index + modelState[0].length);
      return /\b(?:what|which|identify|name|report|tell|show|inspect|check|verify)\b/i.test(beforeModel) ||
        /^\s*(?:is|was)\s+(?:(?:it|this|that)\s+)?(?:the\s+)?$/i.test(beforeModel) ||
        /^\s*[,:\u2014-]\s*(?:is|was|what|which)\b/i.test(afterModel);
    });
  const asksStatus =
    !rejectsStatus &&
    (/\bpixel_ods_status\b/i.test(text) ||
      asksModelState ||
      text.split(/[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i).some(
        (clause) => {
          const hasStatusKeyword =
            /\b(?:ODS|Pixel)\b.{0,80}\b(?:health|status|online|service count|services online|context (?:window|length|limit))\b/i.test(
              clause
            ) ||
            /\b(?:health|status|online|service count|services online|context (?:window|length|limit))\b.{0,80}\b(?:ODS|Pixel)\b/i.test(
              clause
            );
          if (!hasStatusKeyword) return false;
          // UI/design context words adjacent to the status keyword indicate a
          // descriptive reference, not a platform health query.
          const statusKeywordMatch = clause.match(
            /\b(?:health|status|online|service count|services online|context (?:window|length|limit))\b/i
          );
          const hasUiDesignContext = statusKeywordMatch
            ? statusKeywordIsUiNounPhrase(clause, statusKeywordMatch.index, statusKeywordMatch[0].length)
            : false;
          // Interrogative verbs that request facts, not UI actions.
          const queryVerb =
            /\b(?:what|which|identify|name|report|tell|show|inspect|check|verify|list)\b/i.test(
              clause
            );
          const copulaQuestion =
            /^\s*(?:is|are|was|were|has|have)\s+(?:the\s+)?(?:ODS|Pixel)\b/i.test(clause);
          if (hasUiDesignContext) return false;
          const bareQuery = /^\s*(?:ODS|Pixel)\s+(?:health|status|online|service count|services online|context (?:window|length|limit))\s*$/i.test(clause);
          return queryVerb || copulaQuestion || bareQuery;
        }
      ) ||
      // Bare queries ("ODS status?") have their question marks consumed by
      // the clause splitter. Retain punctuation for the remaining question
      // forms, and apply the UI check to the same sentence.
      (text.match(/[^.!?]*[.!?]|[^.!?]*\n/g) ?? []).some((s) =>
        (/\b(?:ODS|Pixel)\b.{0,80}\b(?:health|status|online|service count|services online|context (?:window|length|limit))\b[^.!?]{0,32}\?/i.test(s) ||
          /\b(?:health|status|online|service count|services online|context (?:window|length|limit))\b.{0,80}\b(?:ODS|Pixel)\b[^.!?]{0,32}\?/i.test(s)) &&
        !/\b(?:preview|component|page|dashboard|message|indicator|display|design|light)\b/i.test(s)
      ) ||
      asksAvailability ||
      asksDockerStatus ||
      asksLiveServiceState);
  // Match a service inventory subject, not a compound noun whose first word
  // happens to be "service" in a document or technical research request.
  const serviceSubject = String.raw`\b(?:ODS\s+services?\b|services?\b(?=\s*(?:[,;:]|$)|\s+(?:is|are|was|were|has|have|that|which|and|or|on|in|with|currently|actually|installed|enabled|disabled|healthy|unhealthy|running|stopped|status)\b))`;
  const serviceState = String.raw`\b(?:installed|enabled|disabled|healthy|unhealthy|running|stopped|status)\b`;
  const asksNamedServiceInventory =
    new RegExp(String.raw`\b(?:which|what|list|show|inspect|audit|inventory|report|tell\s+me)\b[^.!?;\n]{0,120}${serviceSubject}[^.!?;\n]{0,120}${serviceState}`, "i").test(text) ||
    new RegExp(String.raw`${serviceSubject}\s+(?:(?:is|are|currently|actually)\s+){0,2}${serviceState}`, "i").test(text);
  // A saved app and ODS may be mentioned in separate instructions, such as
  // "Preserve existing apps. Publish through the ODS preview." Require an
  // actual inventory request in the same clause before forcing a projection.
  const asksOdsAppInventory = text.split(/[!?;\n]+|\.(?=\s|$)/).some((clause) =>
    !/^\s*(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|omit)\b/i.test(clause) &&
    (/\bODS\b.{0,80}\b(?:apps?|applications?)\b/i.test(clause) ||
      /\b(?:apps?|applications?)\b.{0,80}\bODS\b/i.test(clause)) &&
    (/(?:^\s*|\b(?:and|then|also)\s+)(?:please\s+)?(?:which|what|where|list|show|inspect|audit|inventory|report|tell\s+me)\b/i.test(clause) ||
      /^\s*(?:please\s+)?ODS\s+(?:apps?|applications?)\s*$/i.test(clause))
  );
  const asksApps =
    !rejectsApps &&
    (/\bpixel_ods_apps_list\b/i.test(text) ||
      asksOdsAppInventory ||
      /\bODS(?:\s+(?:app|application|service)s?)?\s+(?:links?|URLs?)\b/i.test(text) ||
      /\bconfigured\s+(?:app\s+)?(?:links?|URLs?)\b.{0,48}\bODS\b/i.test(text) ||
      text.split(/[!?;\n]+|\.(?=\s|$)|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i).some(
        (clause) => {
          // A service excluded from research and an unrelated address or URL
          // must not force application inventory before the owner's task.
          const service = String.raw`(?:n8n|Open\s*WebUI|Perplexica|SearXNG|LiteLLM|Hermes)`;
          return new RegExp(`\\b${service}\\b`, "i").test(clause) &&
            /\b(?:configured|link|URL|where|address)\b/i.test(clause) &&
            !explicitlyRejectsOdsTool(clause, service);
        }
      ) ||
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
  if (!explicitCatalog && userMessageExtensionLifecycleIntent(messages, prompt)) return false;
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
    typeof submittedAction?.target !== "string" ||
    step.target !== submittedAction.target ||
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
  // File extensions and a later request to report source hashes are unrelated
  // to installed ODS extensions. Do not combine those clauses into host work.
  const clauses = text.split(/[!?;\n]+|\.(?=\s|$)/).filter((clause) =>
    !/^\s*(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|omit)\b/i.test(clause) &&
    !/\b(?:file|filename)\s+extensions?\b/i.test(clause));
  return clauses.some((clause) =>
    /\b(?:ODS\s+)?extensions?\b/i.test(clause) &&
    extensionState.test(clause) && inventoryIntent.test(clause)
  ) || (
    // Preserve ordinary follow-ups: "Use n8n as an ODS extension. Check
    // whether it is installed." A file suffix cannot establish this scope.
    clauses.some((clause) => /\bODS\s+extensions?\b/i.test(clause)) &&
    clauses.some((clause) =>
      /\b(?:check|verify|which|what|list|show|inspect|report|tell\s+me)\b/i.test(clause) &&
      /\b(?:installed|enabled|disabled|healthy|unhealthy|running|stopped)\b/i.test(clause))
  );
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
  const text = currentOwnerIntentText(messages, prompt);
  if (!text) return undefined;
  const match = text.match(
    /\b(install|enable|disable|remove|uninstall)\s+(?:the\s+)?(?:(?:installed|existing|enabled|disabled)\s+)?(?:ODS\s+)?extension\s+(?:(?:with\s+)?(?:the\s+)?(?:exact\s+)?id\s+)?[`"']?([a-z0-9](?:[a-z0-9_-]|\.(?=[a-z0-9])){0,63})(?![a-z0-9_-]|\.(?=[a-z0-9]))[`"']?/i
  ) ?? text.match(
    /\b(install|enable|disable|remove|uninstall)\s+(?:the\s+)?[`"']?((?!ODS\b|extension\b)[a-z0-9](?:[a-z0-9_-]|\.(?=[a-z0-9])){0,63})[`"']?\s+(?:as\s+(?:an?\s+)?|(?:as\s+)?the\s+)?(?:ODS\s+)?extension\b/i
  );
  if (!match) return undefined;
  // Naming the extension before its type is ordinary owner language. It
  // still selects one exact ID and the same inspected, approval-bound action.
  // A quoted explanation or negated request must not select a lifecycle task.
  const prefix = text.slice(0, match.index).split(/[!?;\n]|\.(?=\s|$)/).at(-1);
  if (/\b(?:not|don['’]t|never|avoid|skip|without|explain|example|tutorial)\b/i.test(prefix) ||
      /[`"']\s*$/.test(prefix)) return undefined;
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
    // An attributive name and its attached address identify one endpoint too:
    // "the server archive at 10.0.0.8". The common address validation below
    // still binds the IP, retains the alias for exclusions, and rejects lists.
    /\b(?:computer|machine|host|device|peer|server)\s+[`"']?([A-Za-z0-9][A-Za-z0-9.:-]{0,252})[`"']?(?=\s+at\s+[`"']?[A-Za-z0-9.:]+)/i,
    // A report such as "the initial Node probe used an old image" names no
    // peer. Treat these words as commands only in an owner directive, and
    // require the complete target to end before a clause boundary or network
    // qualifier. A coordinated verb ("resolve and record") or a longer
    // object phrase is not an endpoint named by its first word.
    /(?:^|[.!?;\n]|\b(?:and(?:\s+then)?|then)\s+)\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|I\s+(?:want|need)\s+you\s+to\s+)?(?:ping|probe|resolve)\s+[`"']?([A-Za-z0-9][A-Za-z0-9.:-]{0,252})[`"']?(?=\s*(?:$|[.!?,;`"'()]|\s+(?:at|on|over|via|for|using|with|through|ports?|and|or|TCP|UDP|SSH|RDP|WinRM|HTTP|HTTPS)\b))/i,
    /\b(?:reachability|connectivity)\s+(?:of|to|for)\s+[`"']?([A-Za-z0-9][A-Za-z0-9.:-]{0,252})[`"']?/i,
    /\b(?:check|inspect|test|verify|probe)\s+(?:(?:a|the|specific|named|remote|LAN|network)\s+)*(?:target|peer)\s*:?\s*(?:(?:named|called|physical)\s+)*[`"']?([A-Za-z0-9][A-Za-z0-9.:-]{0,252})[`"']?/i,
  ];
  // Collect owner-named endpoints rather than silently choosing the first.
  // A directly attached "alias at IP" describes one endpoint; other named
  // endpoints require clarification. Addresses elsewhere in the prose do not
  // override a target or authorize an additional connection.
  const matches = patterns.flatMap((pattern) =>
    [...text.matchAll(new RegExp(pattern.source, `${pattern.flags}g`))]);
  const candidates = [];
  for (const match of matches) {
    const prefix = text.slice(0, match.index).split(/[!?;\n]|\.(?=\s|$)/).at(-1);
    if (/^\s*(?:but\s+)?(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|omit|exclude|without)\b/i.test(prefix)) continue;
    let peer = match[1];
    const aliases = [peer];
    let end = match.index + match[0].length;
    const address = text.slice(end).match(/^\s+at\s+[`"']?([A-Za-z0-9.:]+)[`"']?/i);
    if (address) {
      const value = address[1].replace(/\.$/, "");
      if (!isIP(value)) return undefined;
      peer = value;
      end += address[0].length;
    }
    // An endpoint list is not an authorization to choose whichever is easiest.
    const extra = text.slice(end).match(/^\s*(?:,\s*|\s+(?:and|or)\s+)[`"']?([A-Za-z0-9][A-Za-z0-9.:-]*)[`"']?(?=\s*(?:,|[.!?;]?(?:$|\n))|\s+(?:on|over|using|ports?|and|or)\b)/i);
    if (extra && !/^(?:TCP|UDP|SSH|RDP|WinRM|HTTP|HTTPS)$/i.test(extra[1])) {
      return undefined;
    }
    if (peer?.endsWith(".") && !peer.includes("..")) {
      // A quoted terminal dot belongs to the DNS target; an unquoted single dot
      // ends the sentence. Leave malformed repeated dots for validation below.
      const start = match.index + match[0].lastIndexOf(peer);
      const quote = text[start - 1];
      const explicitlyQuoted = ["`", '"', "'"].includes(quote) &&
        text[start + peer.length] === quote;
      if (!explicitlyQuoted) peer = peer.slice(0, -1);
    }
    if (
      !peer ||
      // Resolving "its log path" names no network peer. Pronouns and articles
      // must not grant a probe or divert ordinary workspace tools into the broker.
      /^(?:a|an|it|its|they|them|their|your|this|that|these|those|the|my|our|local|ODS|Pixel|computer|machine|host|system|network|Tailscale)$/i.test(peer) ||
      peer.includes("..") ||
      peer.split(".").some((label) => label.startsWith("-") || label.endsWith("-"))
    ) {
      return undefined;
    }
    if ([peer, ...aliases].some((name) => {
      const escapedPeer = name.replace(/\.$/, "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      return new RegExp(
        `\\b(?:do\\s+not|don['’]t|never|must\\s+not|should\\s+not)\\b[^.!?;\\n]{0,64}` +
          `\\b(?:contact|access|inspect|query|connect|ping|probe|resolve)?\\s*${escapedPeer}\\b`,
        "i"
      ).test(text);
    })) {
      return undefined;
    }
    if (isIP(peer) && !privatePeerAddressScope(peer)) return undefined;
    candidates.push(peer);
  }
  const unique = new Map(candidates.map((peer) => [peer.toLowerCase().replace(/\.$/, ""), peer]));
  if (unique.size !== 1) return undefined;
  const peer = [...unique.values()][0];
  const ports = [];
  const explicit = text.match(
    /\bports?\s+((?:[0-9]{1,5}(?:\s*(?:,|and)\s*[0-9]{1,5}){0,7}))/i
  )?.[1] ?? "";
  for (const value of explicit.match(/\b[0-9]{1,5}\b/g) ?? []) {
    const port = Number(value);
    if (port >= 1 && port <= 65535 && !ports.includes(port)) ports.push(port);
    if (ports.length === 8) break;
  }
  const protocolRequest = text.split(/[.!?;\n]+|,\s*(?=(?:but|however|instead)\b)/i)
    .filter((clause) => !/^\s*(?:but\s+)?(?:please\s+)?(?:no|do\s+not|don't|never|avoid|skip)\b/i.test(clause)).join(" ");
  if (/\bSSH\b/i.test(protocolRequest) && !ports.includes(22)) ports.push(22);
  if (/\b(?:RDP|Remote Desktop)\b/i.test(protocolRequest) && !ports.includes(3389)) ports.push(3389);
  if (/\bWinRM\b/i.test(protocolRequest)) {
    for (const port of [5985, 5986]) if (!ports.includes(port)) ports.push(port);
  }
  return {
    peer,
    ports: (ports.length ? ports : [...DEFAULT_NETWORK_PEER_PORTS]).slice(0, 8),
  };
}

function withoutFilesystemPaths(text) {
  // A pathname is data, never a device or host-facet authorization. Keep the
  // original request intact for command/path binding; mask only this intent view.
  return text.replace(/([`"'])(?:[A-Za-z]:[\\/]|\\\\|\.{0,2}\/|~\/)[^\r\n]*?\1/g, " ")
    .replace(/(?:^|\s)(?:[A-Za-z]:[\\/]|\\\\|\.{0,2}\/|~\/)[^\s`"']+/g, " ");
}

function localInspectionTextBesidePeer(text) {
  // Remote identity and background route lookups cannot become compulsory
  // observations of this computer. Keep independent positive local requests,
  // including a later "Report CPU and RAM" refinement of a local inspection.
  const localHost = /\b(?:this|my|our|local|current|ODS)\s+(?:host|machine|computer|system|laptop|notebook|desktop|pc)\b/i;
  const request = /\b(?:what|which|tell|show|report|check|verify|identify|inspect|give|get|read|return|measure|explore|inventory|survey|examine|describe)\b/i;
  const clauses = text.split(/[!?;\n]+|\.(?=\s|$)|\b(?:and|then|separately)\s+(?=(?:check|inspect|report|verify|probe|resolve|ping|show|read|measure)\b)/i);
  const positive = clauses.filter((clause) => request.test(clause) &&
    !/^\s*(?:but\s+)?(?:please\s+)?(?:no|do\s+not|don't|never|avoid|skip|omit|exclude)\b/i.test(clause) &&
    !/\b(?:remote|peer|target|SSH|reachability|connectivity|ping|probe|resolve)\b/i.test(clause));
  return positive.some((clause) => localHost.test(clause)) ? positive.join(". ") : "";
}

export function userMessageOperationsRequirements(messages, prompt = undefined) {
  // Pixel Edge appends trusted delivery/routing guidance beside the owner
  // message for small local models. That guidance is not owner intent: words
  // such as "route" must not silently expand a bounded host request into
  // network-route/listener work.
  const text = withoutFilesystemPaths(currentOwnerIntentText(messages, prompt));
  if (!text) return { required: false, actions: [] };
  const hostCommand = userMessageRequestsHostCommand(messages, prompt);
  const networkPeer = hostCommand ? undefined : userMessageNetworkPeerRequest(messages, prompt);
  const hostText = networkPeer ? localInspectionTextBesidePeer(text) : text;
  const explicitOperations =
    /\b(?:use|using|via|through|with)\b.{0,48}\b(?:Pixel\s+)?Operations(?:\s+(?:Broker|capabilit(?:y|ies)|tools?))?\b/i.test(
      text
    );
  const capabilityInventory = userMessageRequestsOperationsCapabilityInventory(
    messages,
    prompt
  );
  const hostEvidenceClauses = hostText.split(
    /[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)/i
  );
  const hostEvidenceClauseNegation =
    /^\s*(?:but\s+)?(?:please\s+)?(?:do\s+not|don['’]t|never|must\s+not|should\s+not|avoid|skip|omit|exclude|no)\b/i;
  // A clause that merely reports what a prior reply did is not a new host-evidence request.
  // Require the non-negated clause to contain request language. Host context may be
  // established in an earlier clause (e.g. "this laptop"); only the evidence facet
  // must be in the requesting clause.
  const hostEvidenceRequest =
    /\b(?:what(?:['’]s|\s+is)?|which|tell|show|report|check|verify|identify|name|inspect|give|get|read|return|measure)\b/i;
  const hostEvidenceFacet =
    /\b(?:hostname|host identity|host platform|kernel|machine architecture|operating[- ]system(?: signature)?|(?:host\s+)?os(?:\s+(?:signature|release))?)\b/i;
  const hostEvidenceLocalHost = /\b(?:ODS|host|machine)\b/i;
  const hostEvidence = hostEvidenceClauses.some((clause) =>
    !hostEvidenceClauseNegation.test(clause) &&
    hostEvidenceRequest.test(clause) &&
    hostEvidenceFacet.test(clause) &&
    (hostEvidenceLocalHost.test(clause) ||
      /\b(?:ODS\s+)?(?:host|machine|computer|system|laptop|notebook|desktop|pc)\b/i.test(hostText))
  );
  const hostContextPattern = /\b(?:ODS\s+)?(?:host|machine|computer|system|laptop|notebook|desktop|pc)\b/i;
  const hostContext = hostContextPattern.test(hostText);
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
  const hostIntentClauses = hostText.split(
    /[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)|\b(?:and|then)\s+(?=(?:find|discover|locate|detect|identify|list|look\s+for|scan)\b)|\band\s+(?=(?:(?:briefly|then)\s+)?(?:explain|summari[sz]e|describe|report)\b)/i
  );
  const artifactOrExplanation = /\b(?:explain|tutorial|example|hypothetical|fictional|pretend|build|create|design|implement|write|preview)\b/i;
  const negatedObservationClause = (clause) => /^\s*(?:but\s+)?(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|omit|exclude)\b/i.test(clause);
  const networkDiscoveryClause = (clause) =>
    /\b(?:LAN|local\s+network)\b/i.test(clause) &&
    /\b(?:computers|machines|hosts|devices|peers)\b/i.test(clause) &&
    /\b(?:find|discover|locate|detect|identify|list|scan|look\s+for|which|what)\b/i.test(clause);
  // Facets in a later "Report CPU and RAM" sentence still refine the same
  // inspection. Only an independent peer-discovery clause is outside the
  // local-host facet scope; do not broaden every multi-sentence host request.
  const localHostFacetText = hostIntentClauses.filter((clause) =>
    !networkDiscoveryClause(clause) && !negatedObservationClause(clause)).join(" ");
  const hostObjectModifiers = "(?:(?:the|this|that|my|your|our|local|actual|real|physical|current|complete|entire|whole|ODS)\\s+)*";
  const hostObjects = [hostContextPattern, ...hostScopeFacetPatterns].map((pattern) => pattern.source).join("|");
  const hostExplorationPattern = new RegExp(
    "\\b(?:explore|inspect|inventory|survey|understand|examine|show\\s+me\\s+around)\\s+" +
      hostObjectModifiers + `(?:${hostObjects})|` +
      "\\b(?:inspection|inventory|survey)\\s+of\\s+" + hostObjectModifiers + hostContextPattern.source,
    "i"
  );
  // Bind an inspection verb to the host or a host facet as its object.
  // Inspecting another object cannot make a later mention of the machine
  // a compulsory inventory. Optional observation permission is separate.
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
    /\b(?:everything|anything)\b.{0,24}\b(?:about|here|on|regarding)\b/i.test(hostText) ||
    /\ball\s+(?:the\s+)?(?:host\s+|machine\s+|computer\s+|system\s+)?(?:details|facts|information)\b/i.test(hostText) ||
    /\b(?:full|complete|comprehensive|broad|thorough)\s+(?:host|machine|computer|system|inspection|inventory|survey|overview|profile)\b/i.test(
      hostText
    );
  // Inspecting live health and then explaining the findings is not a how-to
  // request. Keep this bounded to read-only health facets, not a full inventory
  // or network disclosure. Artifact and negation clauses grant no authority.
  const hostHealthInspection = !hostScopeFacetPatterns
    .some((pattern) => pattern.test(localHostFacetText)) && hostIntentClauses.some((clause) =>
    hostContextPattern.test(clause) &&
    /\b(?:inspect|check|examine|measure)\b/i.test(clause) &&
    /\b(?:health|unhealthy|resource pressure)\b/i.test(clause) &&
    !artifactOrExplanation.test(clause) && !negatedObservationClause(clause));
  const broadHostExploration = hostContext && (hostExplorationIntent || naturalHostOverview) &&
    (broadScopeIntent || (!hostHealthInspection &&
      !hostScopeFacetPatterns.some((pattern) => pattern.test(localHostFacetText))));
  const extensionCatalog = userMessageRequestsExtensionCatalog(messages, prompt);
  const extensionInventory = userMessageRequestsExtensionInventory(messages, prompt);
  const extensionLifecycle = userMessageExtensionLifecycleIntent(messages, prompt);
  const networkDiscoveryRequested = !hostCommand && !networkPeer && hostIntentClauses.some((clause) =>
    networkDiscoveryClause(clause) &&
    !artifactOrExplanation.test(clause) &&
    !negatedObservationClause(clause)) &&
    !explicitlyExcludesHostObservation(hostText, "LAN|local\\s+network|network");
  const localNetworkOverview = !hostCommand && !networkPeer && hostIntentClauses.some((clause) =>
    /\b(?:LAN|local\s+network|(?:host|machine|computer|system|my|our|this)\s+network)\b/i.test(clause) &&
    /\b(?:inspect|explore|check|survey|examine|report|show)\b/i.test(clause) &&
    !/\b(?:interfaces?|addresses?|routes?|routing|ports?|listeners?)\b/i.test(clause) &&
    !artifactOrExplanation.test(clause) && !negatedObservationClause(clause));
  const actions = [];
  if (hostHealthInspection) {
    actions.push("host.uptime", "host.services", "host.cpu", "host.memory", "host.storage");
  }
  if (
    /\b(?:hostname|host identity)\b/i.test(hostText) ||
    (hostContext && /\b(?:machine|system)?\s*identity\b/i.test(hostText))
  ) {
    actions.push("host.identity");
  }
  if (/\bkernel\b/i.test(hostText)) actions.push("host.kernel");
  if (/\b(?:machine architecture|architecture|cpu architecture)\b/i.test(hostText)) {
    actions.push("host.architecture");
  }
  if (/\bhost platform\b/i.test(hostText)) actions.push("host.platform");
  if (/\b(?:operating[- ]system(?: signature)?|(?:host\s+)?os(?:\s+(?:signature|release))?|linux distribution|distro)\b/i.test(hostText)) {
    actions.push("host.os-release");
  }
  if (broadHostExploration || (hostContext && /\b(?:uptime|load averages?|system load)\b/i.test(hostText))) {
    actions.push("host.uptime");
  }
  if (broadHostExploration || (hostContext && /\b(?:process|processes|process inventory)\b/i.test(hostText))) {
    actions.push("host.processes");
  }
  if (broadHostExploration || (hostContext && /\b(?:systemd|(?:system\s+)?services?|service inventory)\b/i.test(hostText))) {
    actions.push("host.services");
  }
  if (broadHostExploration || (hostContext && /\b(?:cpu|processor|hardware)\b/i.test(hostText))) {
    actions.push("host.cpu");
  }
  if (broadHostExploration || (hostContext && /\b(?:gpu|graphics(?:\s+(?:card|processor))?|video\s+card)\b/i.test(hostText))) {
    actions.push("host.gpu");
  }
  if (broadHostExploration || (hostContext && /\b(?:memory|ram|swap)\b/i.test(hostText))) {
    actions.push("host.memory");
  }
  if (broadHostExploration || (hostContext && /\b(?:disk|filesystem|storage|mounts?)\b/i.test(hostText))) {
    actions.push("host.storage");
  }
  if (broadHostExploration || networkDiscoveryRequested || localNetworkOverview || (hostContext && /\b(?:network interfaces?|interfaces?|addresses?|ip addresses?)\b/i.test(hostText))) {
    actions.push("host.network-addresses");
  }
  if (broadHostExploration || networkDiscoveryRequested || localNetworkOverview || (hostContext && /\b(?:routes?|routing)\b/i.test(hostText))) {
    actions.push("host.network-routes");
  }
  if (
    broadHostExploration ||
    (hostContext && /\b(?:ports?|listeners?|listening endpoints?)\b/i.test(hostText))
  ) {
    actions.push("host.listening-ports");
  }
  if (broadHostExploration || (hostContext && /\btailscale\b/i.test(hostText))) {
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
    const facetPattern = HOST_OBSERVATION_FACETS.get(action);
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

function hasWorkspaceHtmlTarget(text) {
  // A public page URL is a navigation target, not a workspace filename.
  const paths = text.replace(/\bhttps?:\/\/[^\s<>"'\x60]+/gi, " ");
  return /\b[A-Za-z0-9_-][A-Za-z0-9._/-]{0,511}\.html?\b/i.test(paths);
}

function ownerForbidsWorkspacePreview(messages, prompt) {
  const text = currentOwnerIntentText(messages, prompt)
    .replace(/(?:\x60{3}|~{3})[\s\S]*?(?:\x60{3}|~{3})/g, " ")
    .replace(/^\s*>[^\n]*/gm, " ")
    .replace(/"[^"\n]*"|\x60[^\x60\n]*\x60/g, " ");
  // Preserve explicit owner constraints without requiring a positive visual
  // vocabulary to use the local snapshot tool. These are delivery actions,
  // not filenames, quoted examples, or another clause's edit restriction.
  return portuguesePreviewForbidden(text) || /\b(?:only|just)\s+(?:the\s+)?(?:code|source(?:\s+code)?)\b/i.test(text) || /\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not|avoid|skip|without)\s+(?:(?:create|build|edit|write|run|execute)\s*(?:,\s*|and\s+|or\s+))*(?:show(?:ing)?|preview(?:ing)?|view(?:ing)?|open(?:ing)?|serv(?:e|ing)|publish(?:ing)?|republish(?:ing)?|display(?:ing)?)\b/i.test(text);
}

function portuguesePreviewForbidden(text) {
  const prose = text.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  return /\b(?:nao|nunca|sem|evite)\s+(?:(?:criar|crie|fazer|faca|editar|edite)\s+(?:e|ou)\s+)?(?:(?:re)?publ(?:ic|iq)\w*|mostr\w*|abrir|abra|preview|pre-?visualiz\w*)\b/i.test(prose)
    || /\b(?:so|somente|apenas)\s+(?:o\s+)?codigo\b/i.test(prose);
}

function portugueseWorkspaceBuildRequest(text) {
  // Match an actual owner command, not quoted examples, tutorials or files
  // merely named by a model. Delivery remains the existing bounded snapshot
  // capability; this adds no network/server or arbitrary-directory authority.
  const prose = text.replace(/(?:`{3}|~{3})[\s\S]*?(?:`{3}|~{3})/g, ' ')
    .replace(/^\s*>[^\n]*/gm, ' ').replace(/"[^"\n]*"|`[^`\n]*`/g, ' ')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  if (portuguesePreviewForbidden(prose)) return false;
  return prose.split(/[!?;\n]+|\.(?=\s|$)/).some(clause => {
    const request = clause.trim().replace(/^(?:(?:ok|ta|agora|entao)[,\s]+)+/i, '')
      .replace(/^por\s+favor[,\s]+/i, '');
    const command = /^(?:(?:voce|vc)\s+)?(?:pode\s+)?(?:crie|cria|criar|faca|faz|fazer|construa|construir|desenvolva|desenvolver|implemente|implementar)\s+/i.test(request);
    const desired = /^(?:eu\s+)?(?:quero|queria|gostaria\s+de)\s+(?:(?:que\s+(?:voce|vc)\s+)?(?:crie|faca|faz|criar|fazer)\s+)?(?:um|uma|outro|outra)\b/i.test(request);
    // Conversational task requests often omit "create": "agora um site em
    // html ...". Require a task prefix and a new visual object, not any mention
    // of HTML in a question, diagnosis, quotation, or already-existing page.
    const elliptical = /^\s*(?:(?:ok|ta)[,\s]+)*(?:agora|entao)[,\s]+/i.test(clause)
      && /^(?:mais\s+)?(?:um|uma|outro|outra)\s+(?:novo\s+|nova\s+)?(?:site|website|pagina\s+web|app\s+web|aplicativo\s+web|jogo|calculadora)\b/i.test(request)
      && !/\b(?:explique|explica|como|por\s+que|porque|significa|existe|existente|esta|ficou|parou|falhou|mostra|abre|carrega|funciona)\b/i.test(request);
    return (command || desired || elliptical)
      && /\b(?:html|site|website|pagina\s+web|app\s+web|aplicativo\s+web|interface|dashboard|landing\s+page|visualizacao|svg)\b/i.test(request)
      && !/\b(?:nao|nunca|sem)\s+(?:criar|crie|fazer|faca|faz|public\w*)\b/i.test(request);
  });
}

function withoutWorkspaceHtmlTargets(text) {
  return text.replace(/\b[A-Za-z0-9_-][A-Za-z0-9._/-]{0,511}\.html?\b/gi, " ");
}

function hasPortugueseWorkspacePreviewDirective(text) {
  const portuguese = text.replace(/(?:`{3}|~{3})[\s\S]*?(?:`{3}|~{3})/g, ' ')
    .replace(/^\s*>[^\n]*/gm, ' ').replace(/"[^"\n]*"|`[^`\n]*`/g, ' ')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  if (!portuguesePreviewForbidden(portuguese)) {
    const directives = portuguese.matchAll(
      /(?:^|[.!?;\n]|\be\s+)\s*(?:(?:depois|entao)\s+)?(?:por\s+favor[, ]+)?(?:(?:so|somente|apenas)\s+)?(?:publique|republique)\s+([^!?;\n]{1,512})/gi
    );
    for (const match of directives) {
      const target = match[1];
      if (hasWorkspaceHtmlTarget(target) || /\b(?:site|website|pagina|preview)\b/i.test(target)) return true;
      // A conditional publication is still a requested delivery, not proof
      // that tests passed. Existing execution/readback gates remain in force.
      if (/^(?:apos|depois\s+de)\b/i.test(target)
        && /\btestes?\b/i.test(target)
        && /\b(?:site|website|pagina|preview)\b/i.test(portuguese.slice(0, match.index))) return true;
    }
  }
  return false;
}

function workspacePreviewInstructionText(text) {
  // This is an intent projection only. Keep the owner's original message and
  // tool contents intact; quoted examples must not become delivery commands.
  let projected = text
    .replace(/(`{3,}|~{3,})[\s\S]*?\1/g, " ")
    .replace(/^[ \t]*>[^\n]*/gm, " ");
  // An explicit payload can be delimited or one unquoted sentence. Preserve
  // independent instructions after its closing quote or sentence boundary.
  // Undelimited multi-sentence prose remains ambiguous; this is not a parser
  // for every way an owner can express a task.
  projected = projected.replace(
    /\b(?:containing|with\s+(?:the\s+)?(?:contents?|text))\s*(?:exactly\s*)?:\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[^\n]*?(?=[!?;\n]|\.(?=\s|$)|$))/gi,
    " "
  );
  const quotedTarget = (value) =>
    /^(?:\.\.?\/)?[A-Za-z0-9_/-][A-Za-z0-9._/-]*\.html?$/i.test(value.trim())
      ? value
      : " ";
  // Preserve a quoted HTML filename as an action target, but not arbitrary
  // quoted prose that happens to contain "portal", "website", or commands.
  return projected
    .replace(/"((?:\\.|[^"\\])*)"|`((?:\\.|[^`\\])*)`/g,
      (_match, quoted, inline) => quotedTarget(quoted ?? inline))
    .replace(/(^|[\s(=,:])'((?:\\.|[^'\\])*)'(?=$|[\s).,;:!?])/g,
      (_match, prefix, quoted) => prefix + quotedTarget(quoted));
}

function hasExplicitWorkspacePreviewDirective(text) {
  if (hasPortugueseWorkspacePreviewDirective(text)) return true;
  // A requested delivery action can follow a diagnosis or code repair. Do not
  // mistake a subordinate "why we should publish" for that owner command.
  const commands = text.matchAll(
    /(?:^|[.!?;\n]|\b(?:and(?:\s+then)?|then)\s+)\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(display|preview|publish|republish|serve|open|show|view)\s+([^!?;\n]{1,512})/gi
  );
  return [...commands].some((match) => {
    const target = match[2].split(/\.(?=\s|$)|\b(?:and|then|but|however|instead)\b/i)[0];
    if (hasWorkspaceHtmlTarget(target)) return true;
    const visualTargetPattern = /\b(?:website|site|web\s*page|frontend|dashboard|preview|animation|illustration|scene|game|chart|diagram|svg)\b/i;
    const visualTarget = visualTargetPattern.test(target);
    // Open/show/view also describe ordinary navigation. Require a local
    // artifact or preview binding before imposing workspace publication.
    if (visualTarget && (!/^(?:open|show|view)$/i.test(match[1]) ||
      /\b(?:workspace|preview)\b/i.test(target) ||
      /\b(?:existing|current|saved|created|built|generated|updated|repaired)\s+(?:animated\s+)?(?:website|site|web\s*page|frontend|dashboard|animation|illustration|scene|game|chart|diagram|svg)\b/i.test(target))) return true;
    // "Edit demo/index.html and publish it" names its target before the
    // command. Bind that pronoun within this clause, not an earlier topic.
    const precedingClause = text.slice(0, match.index)
      .split(/[!?;\n]|\.(?=\s|$)/).at(-1);
    return /^(?:it|this|that)(?:\s|[.!?;]|$)/i.test(target.trim()) &&
      (hasWorkspaceHtmlTarget(precedingClause) ||
        (/\b(?:browser|preview)\b/i.test(target) &&
          visualTargetPattern.test(precedingClause) &&
          /\b(?:build|create|design|generate|make|write)\b/i.test(precedingClause)));
  });
}

function requestsNamedSessionPreview(text, preview) {
  if (!text || !preview?.relativeDirectory) return false;
  // A verified project name is a usable target: owners need not repeat
  // "website" or its full HTML path on every publication request.
  const ownerText = text
    .replace(/(?:\x60{3}|~{3})[\s\S]*?(?:\x60{3}|~{3})/g, " ")
    .replace(/^\s*>[^\n]*/gm, " ");
  const directory = preview.relativeDirectory.replace(/[^A-Za-z0-9_/-]/g, "\\$&");
  const target = new RegExp(
    "^(?:(?:the|this|that|existing|current|updated|repaired)\\s+)*" +
      "[\\x60\"']?(?:/workspace/)?" + directory +
      "(?:/index\\.html)?[\\x60\"']?(?=\\s|[!?;]|\\.(?:\\s|$)|$)",
    "i"
  );
  // Keep this an owner command, rather than a quoted command, explanation,
  // subordinate clause, or instruction found inside a code block.
  const commands = ownerText.matchAll(
    /(?:^|[.!?;\n]|\b(?:and(?:\s+then)?|then)\s+)\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|I\s+(?:want|need)\s+you\s+to\s+)?(?:publish|republish|preview|display|show|open|view)\s+([^!?;\n]{1,512})/gi
  );
  // A bare reference can reopen this session's actual verified artifact.
  // Extra names or clauses (for example a public site's research request)
  // must not inherit that artifact merely because the session has one.
  const bareTarget = /^(?:me\s+)?(?:(?:the|this|that)\s+)(?:website|site|web\s*page|frontend|dashboard|preview|artwork|animation|illustration|scene|game|chart|diagram|svg)(?:\s+(?:again|here))?\s*[.!?]?\s*$/i;
  if (![...commands].some((match) => target.test(match[1]) || bareTarget.test(match[1]))) return false;
  return !/\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not|avoid|skip|without)\s+(?:publish(?:ing)?|republish(?:ing)?|preview(?:ing)?|display(?:ing)?|show(?:ing)?|open(?:ing)?|view(?:ing)?)\b/i.test(ownerText);
}

export function userMessageRequestsWorkspacePreview(messages, prompt = undefined) {
  const text = workspacePreviewInstructionText(currentOwnerIntentText(messages, prompt));
  if (!text) return false;
  if (portuguesePreviewForbidden(text)) return false;
  // Classify visual targets and actions from the same positive request text.
  // A no-website constraint on a Python task is not a website request. Keep
  // independent actions after "but", "instead", "then", or a sentence boundary.
  const actionText = text.replace(
    /\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not|not\s+a\s+request\s+to|avoid|skip|without|no)\b(?:(?!\b(?:but|instead|then)\b)[^.!?;\n])*/gi,
    " "
  ).replace(
    /\b(?:preserve|keep)\s+(?:(?:all|my|the|these|those|other|existing|current|saved|working)\s+)*(?:apps?|applications?)\b(?:\s+unchanged)?/gi,
    " "
  );
  // Driving a browser against an existing page is a tool operation. Do not
  // replace its result with a demand to create and publish new site files.
  // An independent build, revision or publication still requests delivery.
  const browserToolUse =
    /\b(?:browser|playwright|chromium)\b[^.!?;\n]{0,64}\b(?:tools?|automation|capabilit(?:y|ies)|profiles?|inspection)\b/i.test(actionText) ||
    /\b(?:use|using)\b[^.!?;\n]{0,40}\b(?:browser|playwright|chromium)\b/i.test(actionText);
  const browserInteraction = /\b(?:click|exercise|interact|test|navigate|browse|open|inspect|inspection|capture|screenshot|snapshot)\b/i.test(actionText);
  const requestedArtifactChange =
    /\b(?:build|create|develop|design|generate|implement|write|edit|fix|repair|modify|update|publish|republish|serve)\b/i.test(actionText);
  if (browserToolUse && browserInteraction && !requestedArtifactChange) return false;
  const websitePattern =
    /\b(?:browser\b[^.!?;\n]{0,32}\bapps?|dashboards?|frontends?|landing\s+pages?|portals?|sites?|web\b[^.!?;\n]{0,32}\bapps?|web\s*pages?|websites?)\b/i;
  const website = websitePattern.test(actionText);
  const browserInterface =
    /\b(?:forms?|user\s+interfaces?|ui\s+demos?|wireframes?)\b/i.test(actionText) ||
    (/\bprototypes?\b/i.test(actionText) &&
      /\b(?:browser|checkout|flow|form|interface|onboarding|screen|sign[- ]?up|ui|ux|web)\b/i.test(actionText));
  const buildAction =
    /\b(?:build|can\s+you\s+(?:build|create|make)|create|develop|design|generate|give\s+me|implement|make|show\s+me|want|would\s+like|write)\b/i;
  // Preserving existing apps is not a visual revision. A separate positive
  // edit/build/delivery directive still supplies its own action below.
  const reviseAction =
    /\b(?:add|change|continue|edit|improve|modify|patch|refresh|remove|republish|speed\s+up|tweak|update|work)\b/i;
  const build = buildAction.test(actionText);
  const revise = reviseAction.test(actionText);
  // Feedback about the previous website must not turn an independent file or
  // scheduled-work request into a mandatory website build.
  const websiteAction = actionText
    .split(/[!?;\n]+|\.(?=\s|$)|\b(?:and|then|but|however|instead)\s+(?=(?:build|create|develop|design|generate|implement|make|write)\b)/i)
    .some((clause) => websitePattern.test(clause) &&
      (buildAction.test(clause) || reviseAction.test(clause)));
  // A timer or another named utility can be explicitly requested as HTML
  // without using a fixed vocabulary of website/app names. Bind its creation
  // to the same sentence so an earlier saved HTML file grants no authority.
  const htmlCreation = actionText
    .split(/[!?;\n]+|\.(?=\s|$)/)
    .some((clause) => buildAction.test(clause) && hasWorkspaceHtmlTarget(clause));
  // An app inventory in one clause does not become a browser-build request
  // because a later clause asks for an unrelated JSON file or workflow.
  const application = actionText
    .split(/[.!?;\n]+|\b(?:and|then|but|however|instead)\s+(?=(?:build|create|develop|design|generate|implement|make|write|add|change|continue|edit|improve|keep|modify|patch|refresh|remove|republish|tweak|update|work)\b)/i)
    .some((clause) => /\b(?:apps?|applications?)\b/i.test(clause) &&
      (buildAction.test(clause) || reviseAction.test(clause)));
  // An output format alone does not require an HTML wrapper. SVG files may
  // be delivered directly; explicit browser publication still requires proof.
  const browserVisual =
    /\b(?:artworks?|animated\s+(?:art|illustrations?|scenes?)|interactive\s+(?:art|charts?|diagrams?))\b/i.test(actionText) ||
    /\b(?:breakout|brick[- ]?breakers?|browser[- ]?games?|canvas\s+(?:demos?|games?)|interactive\s+(?:demos?|experiences?|visuali[sz]ations?)|task\s+boards?|to-?do\s+(?:apps?|boards?|lists?)|video\s*games?|videogames?|visual\s+(?:demos?|showcases?)|visuali[sz]ations?|voxel(?:[- ](?:based|styles?))?|webgl\s+(?:demos?|scenes?))\b/i.test(actionText) ||
    /\b(?:arcade|board|card|puzzle|racing|rhythm|strategy|word)?\s*games?\b/i.test(actionText);
  const explicitBrowser =
    website || /\b(?:browser|canvas|html|svg|webgl)\b/i.test(actionText);
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
  // A target named review/index.html or backend/status.html does not turn
  // an owner's publication request into a review or backend-only task.
  // Conversely, build/index.html cannot make an explanation a build request.
  const proseText = withoutWorkspaceHtmlTargets(text);
  const proseActionText = withoutWorkspaceHtmlTargets(actionText);
  const explanatoryOnly =
    /\b(?:explain|history|review|tutorial|what\s+is|why)\b/i.test(proseText) &&
    !/\b(?:build|create|develop|design|generate|implement|make)\b/i.test(proseText);
  const nonVisualImplementation =
    /\b(?:backend|daemon|engine|file\s+format|library|parser|renderer|seriali[sz]er|server|service)\b/i.test(proseActionText) &&
    !/\b(?:browser|demo|interactive|visuali[sz]ation)\b/i.test(actionText);
  // Portuguese "no preview" means "in the preview", not English negation.
  const explicitDelivery = hasPortugueseWorkspacePreviewDirective(text) || hasExplicitWorkspacePreviewDirective(actionText);
  // An edit constraint does not veto an independently requested display.
  // Keep negative compound requests closed ("never create and publish").
  // A filename's dot is not a sentence boundary.
  const independentDelivery = explicitDelivery && text
    .split(/[!?;\n]+|\.(?=\s|$)/)
    .some((clause) =>
      !/\b(?:do\s+not|don['’]t|never|must\s+not|should\s+not|avoid|skip|without)\b/i.test(clause) &&
      hasExplicitWorkspacePreviewDirective(clause));
  if (
    rejectsPreview ||
    (rejectsCreation && !independentDelivery) ||
    nativeOnly ||
    (!explicitDelivery && (explanatoryOnly || nonVisualImplementation))
  ) return false;
  const directPreview =
    explicitDelivery ||
    // A dot inside index.html is part of the requested filename, not a
    // sentence boundary between the preview action and its target.
    // Delivery verbs in a rejected list ("do not edit, publish, or run")
    // are constraints, even when another clause names an HTML file.
    (hasWorkspaceHtmlTarget(actionText) &&
      /\b(?:preview|publish|serve|open|show|view)\b/i.test(actionText)) ||
    /\b(?:preview|publish|republish|serve)\b[^.!?;\n]{0,96}\b(?:artworks?|illustrations?|charts?|diagrams?|animations?|games?|sites?|websites?|web\s*pages?|frontends?)\b/i.test(actionText) ||
    /\b(?:site|website|web\s*page|frontend)\b[^.!?;\n]{0,96}\b(?:preview|publish|republish|serve)\b/i.test(actionText);
  const unreachableLocalPreview =
    /\b(?:localhost|local\s+host)\b/i.test(text) &&
    /\b(?:not\s+(?:seeing|loading|opening|working)|can(?:not|'t)\s+(?:see|load|open|reach)|investigate|fix)\b/i.test(text);
  const interactiveDelivery =
    freshWorkspaceCreationRequested(text) &&
    /\b(?:show|display|preview)\b[^.!?;\n]{0,64}\bhere\b/i.test(text) &&
    /\b(?:controls?|interacti(?:ve|on)|keyboard|mobile|phone|touch)\b/i.test(text);
  return directPreview || unreachableLocalPreview || interactiveDelivery ||
    websiteAction ||
    ((application || browserInterface || htmlCreation) && (build || revise)) ||
    (browserVisual && build) || portugueseWorkspaceBuildRequest(text);
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
    /\b(?:existing|previous|prior|already[- ]created)\b[^.!?;\n]{0,96}\b(?:directory|folder)\b/i.test(text) ||
    // Publishing a repaired named HTML artifact is reuse even when the edit
    // instruction says "make the title fit". It need not rewrite every file.
    (hasWorkspaceHtmlTarget(text) &&
      /\b(?:existing|previous|prior|already[- ]created|updated|corrected|repaired|revised)\b/i.test(text)) ||
    /\b(?:existing|previous|prior|already[- ]created)\b[^.!?;\n]{0,48}\b(?:apps?|applications?|artwork|animation|chart|design|diagram|files?|game|illustration|site|website)\b/i.test(text) ||
    /\b(?:apps?|applications?|artwork|animation|chart|diagram|game|illustration|site|website)\b[^.!?;\n]{0,48}\bin\s+(?:(?:my|the|our)\s+)?workspace\b/i.test(text) ||
    /\b(?:show|open|view|preview)\s+(?:me\s+)?(?:the|that|this|our|my)\b[^.!?;\n]{0,64}\b(?:apps?|applications?|artwork|animation|chart|diagram|game|illustration|site|website)\b/i.test(text);
  if (reuseExisting && !explicitCreation) return false;
  return (create.test(text) || portugueseWorkspaceBuildRequest(text)) && !rejectsCreation.test(text);
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
  // Keeping an app as-is is preservation, not a request to edit a prior preview.
  // Other change verbs in the same request still identify a visual revision.
  const action =
    /\b(?:add|animate|change|continue|edit|improve|make|modify|polish|refresh|remove|republish|restyle|rework|speed\s+up|tweak|update)\b/i;
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

function workspacePreviewRequiresAuthoredSnapshot(state, directory) {
  // After a verified snapshot, further checks/repairs operate on an existing
  // artifact. Require a new host receipt without claiming all bytes were
  // newly authored; the old model-content buffer was released at publication.
  if (state?.workspacePreviewVerifiedDirectory === directory) return false;
  const entryPath = `${directory}/index.html`;
  // An inspected entry and successful edit in the same directory establish
  // an existing-file revision even when its wording sounded like creation.
  // Keep strict byte provenance for files this run actually wrote; an edit
  // is not a claim of authorship for every byte in a pre-existing snapshot.
  const inspectedExisting = state?.successfulReadPaths.has(entryPath) &&
    !state?.successfulWritePaths.has(entryPath);
  const editedExisting = [...(state?.successfulEditPaths ?? [])].some(
    (file) => file.startsWith(`${directory}/`)
  );
  return Boolean(state?.workspacePreviewAuthorshipRequired && !(inspectedExisting && editedExisting));
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

function workspacePreviewAuthorshipMatches(state, preview) {
  if (!workspacePreviewRequiresAuthoredSnapshot(state, preview.relativeDirectory)) return false;
  const authored = workspacePreviewAuthoredSnapshot(state, preview.relativeDirectory);
  const entryPath = `${preview.relativeDirectory}/index.html`;
  const entryContent = state.successfulWriteContentByPath.get(entryPath);
  return Boolean(
    authored &&
    authored.paths.length === preview.files &&
    authored.bytes === preview.bytes &&
    authored.sha256 === preview.sha256 &&
    typeof entryContent === "string" &&
    createHash("sha256").update(entryContent, "utf8").digest("hex") === preview.entrySha256
  );
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
  if (
    state?.workspaceVisualContinuationRequested &&
    details.sha256 === state.workspaceVisualContinuationOriginalSha256
  ) return undefined;
  // The trusted host verifies the complete snapshot, including preserved
  // files and assets produced by other tools or previous turns. Current-run
  // write provenance determines authorship attribution, not publication.
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

// Browser access comes from owner configuration, never model claims.
export function privateBrowserAccessForAgent(config, agentId = "pixel") {
  if (config?.browser?.enabled !== true || config?.plugins?.enabled === false ||
      config?.plugins?.entries?.browser?.enabled === false) return false;
  if (Array.isArray(config?.plugins?.allow) &&
      !config.plugins.allow.includes("browser")) return false;
  const agent = config?.agents?.list?.find((entry) => entry?.id === agentId);
  if (!agent) return false;
  const allowsBrowser = (policy) => {
    if (policy?.deny?.some((name) => name === "browser" || name === "*")) return false;
    return !Array.isArray(policy?.allow) ||
      policy.allow.some((name) => name === "browser" || name === "*") ||
      policy.alsoAllow?.includes("browser") === true;
  };
  if (!allowsBrowser(config.tools) || !allowsBrowser(agent.tools)) return false;
  const defaults = config.agents.defaults?.sandbox ?? {};
  const sandbox = agent.sandbox ?? {};
  if ((sandbox.mode ?? defaults.mode ?? "off") === "off") return true;
  if (!allowsBrowser(config.tools?.sandbox?.tools) ||
      !allowsBrowser(agent.tools?.sandbox?.tools)) return false;
  return (sandbox.browser?.enabled ?? defaults.browser?.enabled) === true ||
    (sandbox.browser?.allowHostControl ?? defaults.browser?.allowHostControl) === true;
}

export function userMessageRequestsExactByteDownload(messages, prompt = undefined) {
  const text = currentOwnerIntentText(messages, prompt);
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
  if (!asksDownload || !asksExactBytes) return false;
  const remoteSource =
    /\b(?:https?|ftps?|sftp|s3):\/\/|\bwww\./i.test(text) ||
    /\b(?:remote|origin\s+server|internet|websites?|webpages?)\b/i.test(text) ||
    /\b(?:of|from|at|on|via)\s+(?:(?:the|a)\s+)?[`"']?(?:server|cloud|endpoint|bucket|localhost|(?:\d{1,3}\.){3}\d{1,3}|(?:[a-z0-9-]+\.)+[a-z]{2,})\b/i.test(text);
  if (remoteSource) return true;
  // Source locality permits ordinary workspace preservation. A local
  // destination alone does not exempt an ambiguous external download.
  const localSource =
    /\b(?:of|from)\s+(?:the\s+)?(?:existing|local|workspace)\b/i.test(text) ||
    /\b(?:of|from)\s+[`"']?\/?workspace\//i.test(text) ||
    /\b(?:of|from)\s+[`"']?(?:\.\/)?[a-z0-9_.-]+(?:\/[a-z0-9_.-]+)+\.[a-z0-9]{1,12}\b/i.test(text);
  return !localSource;
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
  const sessionRuns = new Map();
  const pendingToolRuns = new Map();
  const sessionPreviews = new Map();
  const sessionDownloadJobs = new Map();

  function rememberSessionDownload(sessionId, jobId) {
    if (typeof sessionId !== "string" || !sessionId || !OPS_JOB_ID.test(jobId)) return;
    const jobs = sessionDownloadJobs.get(sessionId) ?? new Set();
    jobs.delete(jobId);
    while (jobs.size >= MAX_SESSION_DOWNLOAD_JOBS) jobs.delete(jobs.values().next().value);
    jobs.add(jobId);
    sessionDownloadJobs.delete(sessionId);
    while (sessionDownloadJobs.size >= MAX_TRACKED_RUNS) {
      sessionDownloadJobs.delete(sessionDownloadJobs.keys().next().value);
    }
    sessionDownloadJobs.set(sessionId, jobs);
  }

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
    verificationFingerprint,
    transport
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
      transport,
    });
  }

  function exhaustedWebBudget(state, toolName) {
    if (!WEB_TOOLS.has(toolName)) return null;
    if (state.total >= effective.total) return "total";
    const kind = toolName === "web_search" ? "search" : "fetch";
    return state[kind] >= effective[kind] ? kind : null;
  }

  function webBudgetReason(budget) {
    if (budget === "search") return WEB_SEARCH_BUDGET_EXHAUSTED_REASON;
    if (budget === "fetch") return WEB_FETCH_BUDGET_EXHAUSTED_REASON;
    return WEB_BUDGET_EXHAUSTED_REASON;
  }

  function stateFor(runId) {
    let state = runs.get(runId);
    if (!state) {
      pruneRuns();
      state = {
        progressBudget: createRunProgressBudget(),
        progressAbortAttempted: false,
        search: 0,
        fetch: 0,
        total: 0,
        webLoopAborted: false,
        webTerminals: new Map(),
        codingExhausted: false,
        codingTerminalBlocks: 0,
        invalidEditCreateBlocks: 0,
        oversizedEditBlocks: 0,
        successfulWritePaths: new Set(),
        successfulEditPaths: new Set(),
        successfulWriteContentByPath: new Map(),
        compareSwapRepairCounts: new Map(),
        successfulReadPaths: new Set(),
        repeatedWriteBlocks: new Map(),
        privateNetworkExhausted: false,
        privateNetworkRequestDenied: false,
        privateNetworkPrompt: false,
        clientCancelled: false,
        fetchedUrls: new Map(),
        githubCanonicalUrl: undefined,
        githubCanonicalSatisfied: false,
        odsRoutingInitialized: false,
        odsRequestedTools: new Set(),
        odsRequiredTools: new Set(),
        exactDownloadRequested: false,
        researchDownloadSubmissions: new Map(),
        exactDownloadRequest: undefined,
        exactDownloadSubmissions: new Map(),
        exactDownloadBrokerObserved: false,
        exactDownloadArtifact: undefined,
        exactDownloadPromotion: undefined,
        exactDownloadPromotionAttempted: false,
        exactDownloadTerminalOutcome: undefined,
        exactDownloadTerminalBlocks: 0,
        operationsRequired: false,
        hostObservationPolicy: undefined,
        hostObservationUsed: false,
        extensionDiscoveryUsed: false,
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
        toolExecutionAttempted: false,
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
        workspacePreviewRequired: false,
        workspacePreviewForbidden: false,
        workspacePreviewAuthorshipRequired: false,
        workspacePreviewModelAuthored: false,
        workspaceVisualContinuationRequested: false,
        workspaceVisualContinuationEdited: false,
        workspaceVisualContinuationOriginalSha256: undefined,
        workspacePreviewInspectionRequested: false,
        workspacePreviewDirectory: undefined,
        workspacePreviewAttempted: false,
        workspacePreview: undefined,
        workspaceLastVerifiedPreview: undefined,
        workspacePreviewVerifiedDirectory: undefined,
        workspaceToolSearchRouted: false,
        workspaceToolSearchQueries: new Set(),
        workspaceInspectionRouted: false,
        workspaceInspectionPollCorrections: 0,
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
        recursiveDeleteDenied: false,
        recursiveDeleteAbortAttempted: false,
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

  function stopExhaustedRun(state, runId) {
    if (!state?.progressBudget.exhausted || state.progressAbortAttempted) return;
    const sessionId = state.currentSessionId;
    if (!sessionId || sessionRuns.get(sessionId) !== runId) return;
    state.progressAbortAttempted = true;
    try { execControl?.signal?.(runId); }
    catch (error) { warn(`Pixel progress-limit execution signal failed: ${String(error)}`); }
    // Do not clear the session or its history. Abort only its active harness
    // run; deliveryVerificationForRun retains the host-authoritative artifacts.
    // Only called at model_call_ended. Aborting from model-start/stream
    // construction can strand the provider prompt and its session write lock.
    // Tool hooks enforce the terminal budget while this boundary is pending.
    try {
      const aborted = abortRun?.(sessionId);
      warn(`Pixel progress-limit abort ${aborted ? "requested" : "not acknowledged"}: ${runId}`);
    } catch (error) { warn(`Pixel progress-limit abort failed: ${String(error)}`); }
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
    const canonicalParams = canonicalWorkspaceParams(toolName, event?.params, runs.get(context?.runId)?.configuredWorkspaceRoot);
    let normalizedParams = normalizeWorkspaceParams(toolName, canonicalParams) ??
      (isDeepStrictEqual(canonicalParams, event?.params) ? undefined : canonicalParams);

    const { runId, sessionId } = runIdentity(event, context);
    // OpenClaw's before_tool_call context may omit sessionId even though the
    // earlier before_prompt_build hook supplied the exact run identity. Keep
    // policy and deterministic routing active from runId alone; operations
    // that truly need a session still fail closed on the optional sessionId.
    const state = runId ? stateFor(runId) : undefined;
    if (state?.progressBudget.exhausted) {
      return { block: true, blockReason: RUN_PROGRESS_STOP_REASON };
    }
    if (state?.workspacePreviewRequired && extensionlessHtmlWrite(toolName, normalizedParams ?? event?.params)) {
      return {block:true, blockReason: 'The write path names a FILE, not a directory. For this website, write the complete HTML to a fresh workspace-relative directory ending in /index.html (for example marketing-site/index.html). Do not write HTML to an extensionless directory name: it would prevent creating files inside it. Preserve any existing file and choose a fresh directory if that name is already a file.'};
    }
    const fileParent = state?.workspacePreviewRequired && workspaceFileParent(toolName, normalizedParams ?? event?.params, state.configuredWorkspaceRoot);
    if (fileParent) {
      return {block:true, blockReason:`Cannot create this site's files: ${fileParent} already exists as a FILE, not a directory. Preserve that file. Choose a fresh sibling directory (for example ${fileParent}-site) and write index.html there, then publish that new relativeDirectory. Do not retry paths inside the existing file or delete it.`};
    }
    // A refusal is terminal for this run, not an invitation to express the
    // same destructive effect through another interpreter or tool. This must
    // precede Tool Search, deferred dispatch and every parameter rewrite.
    // It contains retries after this tripwire; it is not a shell sandbox or
    // a guarantee against an unrecognized first destructive command.
    if (state?.recursiveDeleteDenied) {
      if (!state.recursiveDeleteAbortAttempted) {
        state.recursiveDeleteAbortAttempted = true;
        try {
          execControl?.signal?.(runId);
        } catch (error) {
          warn(`Pixel deletion-refusal execution signal failed for run ${runId}: ${String(error)}`);
        }
        // Failure to signal an existing command must not skip model abort.
        try {
          const activeSession = sessionId ?? state.currentSessionId;
          if (typeof activeSession === "string" && activeSession) abortRun?.(activeSession);
        } catch (error) {
          warn(`Pixel deletion-refusal abort failed for run ${runId}: ${String(error)}`);
        }
      }
      return { block: true, blockReason: RECURSIVE_DELETE_REQUIRES_OWNER_REASON };
    }
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
      if (!state.ownerIntentObserved || state.workspacePreviewForbidden) {
        return {
          block: true,
          blockReason:
            "The assistant blocked workspace publication because the current owner request is unavailable or explicitly prohibits displaying a preview.",
        };
      }
      const suppliedArgs =
        toolName === "tool_call" ? pendingParams?.args : pendingParams;
      if (suppliedArgs?.scaffold !== undefined) {
        return {
          block: true,
          blockReason:
            "The assistant blocked an ODS-authored creative scaffold. The active model must create the owner's requested artifact with workspace tools, then publish that exact directory.",
        };
      }
      const args = suppliedArgs;
      if (Object.hasOwn(args ?? {}, 'path')) {
        return {block:true, blockReason:'Supply one exact relativeDirectory for the preview, not path together with other fields.'};
      }
      const hasDirectory = Object.hasOwn(args ?? {}, "directory");
      const hasRelativeDirectory = Object.hasOwn(args ?? {}, "relativeDirectory");
      const providedDirectory = normalizeWorkspaceFilePath(
        hasRelativeDirectory ? args.relativeDirectory : args?.directory
      );
      if (hasDirectory && hasRelativeDirectory &&
          normalizeWorkspaceFilePath(args.directory) !== providedDirectory) {
        return { block: true, blockReason: "The assistant blocked conflicting preview directories. Supply one exact relativeDirectory." };
      }
      const observedDirectory = workspacePreviewDirectoryFromState(state);
      // An explicit target must not be silently replaced by a previous one.
      // A static subdirectory may be selected after the parent failed validation.
      const directory = hasRelativeDirectory || hasDirectory
        ? providedDirectory
        : observedDirectory;
      const validDirectory = typeof directory === "string" && directory.length > 0 &&
        directory.split("/").every((part) => WORKSPACE_PATH_COMPONENT.test(part));
      const requiresAuthoredSnapshot = workspacePreviewRequiresAuthoredSnapshot(state, directory);
      const hasObservedIndex = validDirectory &&
        (state.successfulWritePaths.has(`${directory}/index.html`) ||
          state.successfulReadPaths.has(`${directory}/index.html`));
      // The host reopens, validates and hashes an existing artifact at publish
      // time. An extra model read is not a file-integrity check and can trap
      // a successful repair in a read/publish retry loop. A read also covers
      // entries created through a build or renamed by exec. Current-run write
      // provenance controls authorship attribution, not permission to publish
      // inspected files. Host receipts remain required for publication.
      const requestedExistingPreview = state.workspacePreviewRequired && !requiresAuthoredSnapshot;
      if (!validDirectory || (!hasObservedIndex && !requestedExistingPreview)) {
        if (validDirectory && !state.workspacePreviewAuthorshipRequired) {
          return { block: true, blockReason: `Read ${directory}/index.html before publishing that exact directory. Preserve existing files; a different directory's readback cannot verify this target.` };
        }
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
    // No broker submission does not mean no work happened. A clean-context
    // replay is safe only before any tool execution was attempted; a failed
    // or disconnected call may still have produced effects. Discovery alone
    // is metadata and can safely precede that recovery.
    if (state && !["tool_search", "tool_describe"].includes(selectedToolName)) {
      state.toolExecutionAttempted = true;
    }
    if (extensionDiscoveryEligible(state) &&
        (selectedToolName === EXTENSION_READ_TOOL || extensionReadSubmission(selectedToolName, selectedParams))) {
      // Read-only discovery is a tool capability, not a prompt-derived plan.
      // Keep actual target/query/ID intact; the broker validates their policy.
      state.extensionDiscoveryUsed = true;
      state.operationsRequired = true;
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
        !["read", "write", "edit", "exec", "process", "tool_search", "tool_describe", WORKSPACE_PREVIEW_TOOL].includes(selectedToolName) ||
        (FILE_PATH_TOOLS.has(selectedToolName) && !insideContinuationDirectory)
      ) {
        return {
          block: true,
          blockReason: WORKSPACE_VISUAL_CONTINUATION_SCOPE_REASON,
        };
      }
      if (
        ["edit", "write"].includes(selectedToolName) &&
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
      const writePath = selectedToolName === "write"
        ? normalizeWorkspaceFilePath(selectedParams?.path)
        : undefined;
      if (writePath && state.successfulWritePaths.has(writePath)) {
        const previousContent =
          state.successfulWriteContentByPath.get(writePath);
        const newContent = selectedParams?.content;
        /* Only block a repeated write when the content is identical
         * (true no-progress loop).  A materially different write is
         * allowed because the file may have been deleted, externally
         * modified, or needs a complete rewrite that CAS cannot handle.
         * The CAS adaptation above still converts bounded workspace-task
         * repairs to compare-and-swap edits when possible. */
        if (
          typeof previousContent === "string" &&
          typeof newContent === "string" &&
          previousContent === newContent
        ) {
          const blocks =
            state.repeatedWriteBlocks.get(writePath) ?? 0;
          state.repeatedWriteBlocks.set(writePath, blocks + 1);
          if (blocks === 0) {
            return {
              block: true,
              blockReason: REPEATED_WRITE_REQUIRES_PATCH_REASON,
            };
          }
          state.codingExhausted = true;
          state.codingTerminalBlocks = 1;
          return {
            block: true,
            blockReason: REPEATED_WRITE_RETRY_EXHAUSTED_REASON,
          };
        }
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
        state.workspacePreviewRequired &&
        selectedToolName === "exec" &&
        execLaunchesWorkspaceServer(selectedParams)
      ) {
        return { block: true, blockReason: WORKSPACE_PREVIEW_REQUIRES_TOOL_REASON };
      }
      const setupDirectory =
        state.workspacePreviewRequired && selectedToolName === "exec"
          ? workspacePreviewMkdirDirectory(selectedParams)
          : undefined;
      if (setupDirectory) {
        return {
          block: true,
          blockReason:
            "The assistant does not need a separate directory-preparation command for this preview. " +
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
          : undefined,
        toolName
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
    if (state?.ownerIntentObserved && state.operationsRequired &&
        ["pixel_ods_status", "pixel_ods_apps_list"].includes(effectiveToolName)) {
      const permitted = effectiveToolName === "pixel_ods_status"
        ? state.hostObservationPolicy?.allowOdsStatus : state.hostObservationPolicy?.allowOdsApps;
      if (!permitted) return { block: true, blockReason: OPERATIONS_NOT_REQUESTED_REASON };
      // Local read-only projections can inform a plan before, between, or after
      // broker jobs. Their receipts still prove only their own metadata.
      return normalizedParams === undefined ? undefined : { params: normalizedParams };
    }

    if (
      state?.workspaceTaskRequested &&
      !state.operationsRequired &&
      state.odsRequiredTools.size === 0 &&
      !state.odsRequestedTools.has(effectiveToolName) &&
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
    if (effectiveToolName === SYNCHRONOUS_HOST_OBSERVE_TOOL) {
      const selected = toolName === "tool_call"
        ? wrappedToolParams?.args : normalizedParams ?? event?.params;
      const params = permittedHostObservationParams(selected, state?.hostObservationPolicy);
      if (!params) return {
        block: true,
        blockReason: "The assistant could not validate this host observation against the current request. " +
          "Check the tool argument schema and any explicit inspection exclusions. A network peer " +
          "must match one unambiguous owner-requested endpoint and its permitted ports; " +
          "if the endpoint is unclear, ask the owner rather than retrying the same call.",
      };
      state.hostObservationUsed = true;
      return toolName === "tool_call"
        ? { params: { id: SYNCHRONOUS_HOST_OBSERVE_TOOL, args: params } }
        : { params };
    }
    if (
      (state?.operationsRequired || state?.hostObservationUsed) &&
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
    if (state?.operationsInventoryOnly && !extensionDiscoveryActive(state)) {
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
      state?.ownerIntentObserved &&
      !state.operationsRequired &&
      !state.exactDownloadRequested &&
      OPERATIONS_TOOLS.has(effectiveToolName) &&
      // Capability metadata is read-only and grants no action authority.
      effectiveToolName !== "pixel_ops_inventory" &&
      // Public downloads are a normal research/development capability. The
      // broker enforces network, size, redirect, and quarantine policy; the
      // promoter independently verifies bytes and a create-only destination.
      // A prompt-routing heuristic must not require an "Operations" task to
      // reach those boundaries or stop later sandbox work.
      effectiveToolName !== "pixel_ops_download_stage" &&
      !(DOWNLOAD_JOB_TOOLS.has(effectiveToolName) &&
        (state.researchDownloadSubmissions.has((toolName === "tool_call"
          ? wrappedToolParams?.args : normalizedParams ?? event?.params)?.jobId) ||
          sessionDownloadJobs.get(state.currentSessionId)?.has((toolName === "tool_call"
            ? wrappedToolParams?.args : normalizedParams ?? event?.params)?.jobId))) &&
      !(["pixel_ops_job_get", "pixel_ops_job_wait"].includes(effectiveToolName) &&
        state.operationsSubmittedJobs.has((toolName === "tool_call"
          ? wrappedToolParams?.args : normalizedParams ?? event?.params)?.jobId))
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

    // Publication verifies a snapshot, not completion of the owner's task.
    // Let verification and repairs reach normal tool/loop checks; a blanket
    // early return here can itself repeat forever before those checks run.

    // Finding and describing the approved broker is not an attempt to replace
    // its verified bytes. Keep discovery subject to the normal loop checks.
    const exactDownloadDiscovery = effectiveToolName === "tool_search" || effectiveToolName === "tool_describe";
    if (state?.exactDownloadRequested && !state.exactDownloadPromotion &&
        !exactDownloadDiscovery && !EXACT_DOWNLOAD_BROKER_TOOLS.has(effectiveToolName)) {
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

    if (state?.exactDownloadRequested && EXACT_DOWNLOAD_BROKER_TOOLS.has(effectiveToolName)) {
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

    // Required host evidence does not impose an order on independent workspace
    // or public research work. Each tool still passes its own checks below;
    // web observations never satisfy a required Operations receipt.
    const operationsMayContinueWithIndependentTools =
      state?.operationsRequired === true &&
      (WORKSPACE_CONTINUATION_TOOLS.has(effectiveToolName) ||
        WEB_TOOLS.has(effectiveToolName));
    if (
      state?.operationsRequired &&
      !extensionDiscoveryActive(state) &&
      !OPERATIONS_TOOLS.has(toolName) &&
      effectiveToolName !== "tool_search" &&
      effectiveToolName !== "tool_describe" &&
      !operationsMayContinueWithIndependentTools
    ) {
      // One model response may contain several parallel tool calls. Return the
      // same correction to every disallowed call in that response instead of
      // treating the second sibling call as a second ignored correction. A
      // later model continuation that still ignores the boundary is aborted.
      state.operationsRoutingBlocks += 1;
      if (
        (state.operationsPromptRound > 0 || state.operationsRoutingBlocks < 4) &&
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

    if (extensionDiscoveryActive(state) && OPERATIONS_SUBMISSION_TOOLS.has(toolName)) {
      const params = normalizedParams ?? event?.params;
      if (!extensionReadSubmission(toolName, params)) {
        return { block: true, blockReason: OPERATIONS_WRONG_ACTION_REASON };
      }
      // An early return here bypasses only the plan-specific rewrites below;
      // OpenClaw tool policy and the external broker still govern this call.
      return normalizedParams === undefined ? undefined : { params: normalizedParams };
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
              !["ready", "inspected"].includes(inspection.result.outcome) ||
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
      if (state) state.recursiveDeleteDenied = true;
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
      state.privateNetworkRequestDenied = true;
      return { block: true, blockReason: PRIVATE_URL_REQUEST_REASON };
    }

    // Denying one destination must not abort an unrelated, permitted tool.
    // Keep the denial recorded across successful pivots so alternating calls
    // cannot reset the repeated-private-access fuse. Explicit private-URL
    // requests retain their separate no-substitution policy.
    const repeatsDeniedPrivateAccess =
      (selectedToolName === "exec" && execTargetsNonPublicAddress(selectedEvent)) ||
      ((selectedToolName === "web_fetch" || selectedToolName === "pixel_ods_web_extract") &&
        fetchTargetsNonPublicAddress(selectedEvent)) ||
      (selectedToolName === "browser" &&
        (urlTargetsNonPublicAddress(selectedParams?.url) ||
          urlTargetsNonPublicAddress(selectedParams?.targetUrl)));
    if (state?.privateNetworkExhausted &&
        (state.privateNetworkRequestDenied || repeatsDeniedPrivateAccess)) {
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

    // Requested read-only projections are useful facts, not prerequisites for
    // discovery, argument recovery, or workspace work. Their execution and
    // results still pass through ordinary tool validation and receipt handling.
    if (state?.odsRequiredTools.has(effectiveToolName)) {
      state.odsRequiredTools.delete(effectiveToolName);
    }

    if (
      (selectedToolName === "web_fetch" || selectedToolName === "pixel_ods_web_extract") &&
      fetchTargetsNonPublicAddress(selectedEvent)
    ) {
      if (state?.privateBrowserAccess && !state.privateBrowserRedirected) {
        state.privateBrowserRedirected = true;
        return { block: true, blockReason: "This public web tool did not access the private URL. Use the configured browser for the owner's requested page; do not substitute shell or another public fetch tool." };
      }
      if (state) state.privateNetworkExhausted = true;
      warn("Pixel blocked a non-public web_fetch destination before execution");
      return { block: true, blockReason: WEB_FETCH_PUBLIC_ONLY_REASON };
    }
    if (selectedToolName === "exec" && execTargetsNonPublicAddress(selectedEvent)) {
      if (state?.privateBrowserAccess && !state.privateBrowserRedirected) {
        state.privateBrowserRedirected = true;
        return { block: true, blockReason: "This shell command did not access the private URL. Use the configured browser for the owner's requested page; do not retry shell or public fetch tools." };
      }
      if (state) state.privateNetworkExhausted = true;
      warn("Pixel blocked an exec-based private HTTP(S) destination before execution");
      return { block: true, blockReason: EXEC_PRIVATE_NETWORK_REASON };
    }

    // Truncation and source selection are research observations, not authority
    // boundaries. Let the model select another page, search, or extraction;
    // the ordinary per-run budget and duplicate-call guard still apply.

    if (WEB_TOOLS.has(selectedToolName) && (!runId || !sessionId)) {
      return {
        block: true,
        blockReason:
          "The assistant could not establish the bounded run identity required for web access. Do not call another tool in this turn; explain that web research is temporarily unavailable.",
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

    // Search and page-reading allowances are independent. Their denial and
    // retry state survive compaction; progress through another permitted tool
    // neither consumes that denial allowance nor resets it. Total exhaustion
    // still applies to every web tool, including resolved Tool Search calls.
    const exhaustedBudget = exhaustedWebBudget(state, effectiveToolName);
    if (exhaustedBudget) {
      const reason = webBudgetReason(exhaustedBudget);
      let terminal = state.webTerminals.get(exhaustedBudget);
      if (!terminal) {
        terminal = { blocks: 0, round: state.operationsPromptRound };
        state.webTerminals.set(exhaustedBudget, terminal);
        return { block: true, blockReason: reason };
      }
      // Sibling calls were already emitted before the model could receive the
      // warning. Escalate only after another observed model decision, as the
      // Operations guards do. Runtimes without model hooks retain the bounded
      // call-count fallback rather than acquiring an unlimited retry allowance.
      if (state.operationsPromptRound > 0 &&
          state.operationsPromptRound === terminal.round) {
        return { block: true, blockReason: reason };
      }
      if (terminal.blocks === 0) {
        terminal.blocks = 1;
        terminal.round = state.operationsPromptRound;
        return { block: true, blockReason: reason };
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
      state.webLoopAborted ||= aborted;
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

    const kind = toolName === "web_search" ? "search" : "fetch";
    state[kind] += 1;
    state.total += 1;
    if (toolName === "web_fetch") {
      const fetchUrl = canonicalFetchUrl(event);
      const requestedChars = event?.params?.maxChars;
      const fetchChars = Number.isSafeInteger(requestedChars) && requestedChars > 0
        ? requestedChars : Infinity;
      // A repeated page consumes one bounded attempt, but does not revoke
      // access to other sources or extraction. Retain this state through
      // compaction so retries neither erase evidence nor reset the run budget.
      // An explicit larger read can recover content omitted by the first
      // window. An omitted/invalid limit cannot establish a larger window.
      if (fetchUrl && state.fetchedUrls.has(fetchUrl) &&
          !(Number.isFinite(fetchChars) && fetchChars > state.fetchedUrls.get(fetchUrl))) {
        return { block: true, blockReason: WEB_FETCH_REPEAT_PIVOT_REASON };
      }
      if (fetchUrl) state.fetchedUrls.set(fetchUrl, fetchChars);
    }
    return normalizedParams ? { params: normalizedParams } : undefined;
  }

  function observeRun(context, agentId = "pixel", event = undefined, capabilities = undefined) {
    if (context?.agentId !== agentId) return;
    const runId = context?.runId;
    const sessionId = context?.sessionId;
    if (
      typeof runId === "string" &&
      runId &&
      typeof sessionId === "string" &&
      sessionId &&
      userMessageRequestsPrivateUrl(event?.messages, event?.prompt) &&
      capabilities?.privateBrowserAccess !== true
    ) {
      stateFor(runId).privateNetworkPrompt = true;
    }
    if (typeof runId === "string" && runId) {
      const state = stateFor(runId);
      if (capabilities !== undefined) {
        state.configuredWorkspaceRoot = capabilities.workspaceRoot;
        state.privateBrowserAccess = capabilities.privateBrowserAccess === true &&
          userMessageRequestsPrivateUrl(event?.messages, event?.prompt);
      }
      if (typeof sessionId === "string" && sessionId) {
        state.currentSessionId = sessionId;
        sessionRuns.delete(sessionId);
        while (sessionRuns.size >= MAX_TRACKED_RUNS) sessionRuns.delete(sessionRuns.keys().next().value);
        sessionRuns.set(sessionId, runId);
      }
      if (typeof context?.sessionKey === "string" && context.sessionKey) {
        state.currentSessionKey = context.sessionKey;
      }
      if (currentUserText(event?.messages, event?.prompt)) {
        state.ownerIntentObserved = true;
        state.workspacePreviewForbidden = ownerForbidsWorkspacePreview(event?.messages, event?.prompt);
        const previousPreview = typeof sessionId === "string" && sessionId
          ? sessionPreviews.get(sessionId)
          : undefined;
        const namedPreviewRequested = requestsNamedSessionPreview(
          currentOwnerIntentText(event?.messages, event?.prompt), previousPreview
        );
        const visualContinuationRequested =
          userMessageRequestsWorkspaceVisualContinuation(
            event?.messages,
            event?.prompt
          );
        const trustedSessionPreview =
          visualContinuationRequested && typeof sessionId === "string" && sessionId
            ? sessionPreviews.get(sessionId)
            : undefined;
        const previewRequested = namedPreviewRequested || userMessageRequestsWorkspacePreview(
          event?.messages, event?.prompt
        );
        const explicitDelivery = namedPreviewRequested || (previewRequested && hasExplicitWorkspacePreviewDirective(
          currentOwnerIntentText(event?.messages, event?.prompt)
        ));
        state.workspaceVisualContinuationRequested = Boolean(trustedSessionPreview);
        if (trustedSessionPreview) {
          state.workspaceVisualContinuationOriginalSha256 ??= trustedSessionPreview.sha256;
        }
        // A prose-only continuation guess is not a workspace access boundary.
        // With no verified previous preview, ordinary tools must remain usable
        // to locate the requested files. Only a real preview binds its scope.
        state.workspacePreviewRequired = !state.workspacePreviewForbidden && (
          Boolean(trustedSessionPreview) || state.workspaceVisualArtifactProduced ||
          ((!visualContinuationRequested || explicitDelivery) && previewRequested)
        );
        state.workspacePreviewAuthorshipRequired = Boolean(
          state.workspacePreviewRequired &&
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
          state.workspacePreviewRequired ||
          userMessageRequestsWorkspaceTools(event?.messages, event?.prompt);
        state.workspaceMutationRequested =
          state.workspacePreviewRequired ||
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
        if (namedPreviewRequested) {
          state.workspacePreviewDirectory = previousPreview.relativeDirectory;
        }
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
        // General verification can be a hash comparison, an inventory read,
        // or a future follow-up. Only an explicit test-run request requires
        // a recognized test-runner receipt. Unknown custom checks are not a
        // formal pass, and recorded failed or pending checks still win.
        state.workspaceVerificationRequested = state.workspaceTaskRequested &&
          (state.workspacePythonUnittestRequested ||
            /\b(?:(?:run|execute)\s+(?:the\s+)?(?:unit\s*)?tests?|test\s+suite)\b/i.test(
              currentOwnerIntentText(event?.messages, event?.prompt) ?? ""
            ));
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
        state.hostObservationPolicy = hostObservationPolicy(event?.messages, event?.prompt);
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
          // Outstanding routing work is consumed by the outer Tool Search hook.
          // Keep the original request for the nested call and subsequent reads.
          state.odsRequestedTools = new Set(requirements);
          state.odsRequiredTools = new Set(requirements);
          state.odsRoutingInitialized = true;
        }
      }
      const githubUrl = userMessageGitHubRepositoryUrl(event?.messages, event?.prompt);
      if (githubUrl) {
        if (!state.githubCanonicalUrl) {
          state.githubCanonicalUrl = githubUrl;
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
    if (context?.agentId !== undefined && context.agentId !== agentId) return;
    const runId = context?.runId ?? event?.runId;
    if (typeof runId !== "string" || !runId) return;
    // OpenClaw 2026.6.33 model-call hooks omit agentId. Bind those events to
    // a Pixel run already registered by observeRun, using its session identity.
    // An unbound lifecycle event must not create a run or advance another agent.
    const state = context?.agentId === agentId ? stateFor(runId) : runs.get(runId);
    if (!state) return;
    if (context?.agentId === undefined) {
      const hasSessionId = typeof context?.sessionId === "string" && context.sessionId.length > 0;
      const hasSessionKey = typeof context?.sessionKey === "string" && context.sessionKey.length > 0;
      if ((!hasSessionId && !hasSessionKey) ||
          (hasSessionId && context.sessionId !== state.currentSessionId) ||
          (hasSessionKey && context.sessionKey !== state.currentSessionKey)) return;
    }
    state.operationsPromptRound += 1;
    state.progressBudget.beginModelRound();
  }

  function observeModelEnd(_event, context, agentId = "pixel") {
    if (context?.agentId && context.agentId !== agentId) return;
    const runId = context?.runId;
    const state = runs.get(runId);
    if (!state || !context?.sessionId || context.sessionId !== state.currentSessionId) return;
    stopExhaustedRun(state, runId);
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
    event = {...event, params: canonicalWorkspaceParams(toolName, event?.params, state.configuredWorkspaceRoot)};
    const pendingToolRun = pendingToolRuns.get(toolCallId);
    // Nested Tool Search executions also emit hooks. Count only the outer
    // call (or an ordinary direct call), never both receipts for one action.
    if ((event?.result || event?.error) && !String(toolCallId).startsWith('tool_search_code:')) {
      const selected = toolName === 'tool_call'
        ? toolSearchSelectedToolEvent(event, 'process', 'core') : toolName === 'process' ? event : undefined;
      const running = selected?.result?.details?.status === 'running' &&
        typeof selected?.params?.sessionId === 'string' &&
        state.pendingExecSessions.has(selected.params.sessionId);
      state.progressBudget.observeResult({callId: toolCallId, tool: toolName,
        params: event.params, failed: failedToolOutcome(event), pending: running});
    }
    if (
      toolName === "tool_call" &&
      ["read", "write", "edit", "apply_patch", "exec", "process", "web_search", "web_fetch"].includes(
        pendingToolRun?.selectedToolName
      )
    ) {
      const envelope = toolSearchEventEnvelope(
        event,
        pendingToolRun.selectedToolName,
        "core"
      );
      if (envelope && pendingToolRun.runId === runId &&
          (!["web_search", "web_fetch"].includes(pendingToolRun.selectedToolName) ||
            isDeepStrictEqual(envelope.params, pendingToolRun.selectedParams))) {
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
    // Tool Search reports the inner execution before its outer tool_call.
    // Apply a bound mutation through the outer receipt once; replaying an edit
    // twice can invalidate correctly tracked bytes and hide a valid preview.
    // The pinned runtime's child ID contains a sanitized parent ID and sequence.
    const matchingParents = directMutation && typeof toolCallId === "string"
      ? [...pendingToolRuns].filter(([parentId, pending]) => {
          if (pending.transport !== "tool_call" || pending.runId !== runId ||
              pending.selectedToolName !== directMutation.name ||
              !isDeepStrictEqual(pending.selectedParams, event.params)) return false;
          const parent = parentId.trim().replace(/[^A-Za-z0-9_.:-]+/g, "_").slice(0, 120) || "call";
          const prefix = `tool_search_code:${parent}:${directMutation.name}:`;
          return toolCallId.startsWith(prefix) && /^[1-9][0-9]*$/.test(toolCallId.slice(prefix.length));
        })
      : [];
    const successfulMutation =
      completedMutation && matchingParents.length !== 1 && !toolCallFailed(completedMutation.event)
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
    const completedExecution = toolName === "exec"
      ? event
      : toolName === "tool_call"
        ? toolSearchSelectedToolEvent(event, "exec", "core")
        : undefined;
    const completedExecFingerprint = execFingerprint(completedExecution?.params);
    const originalExecFingerprint = state.execOriginalByWrapped.get(completedExecFingerprint);
    const completedCommand = pendingToolRun?.runId === runId && pendingToolRun.selectedToolName === 'exec'
      ? pendingToolRun.selectedParams?.command
      : originalExecFingerprint ? JSON.parse(originalExecFingerprint)[0] : completedExecution?.params?.command;
    if (state.workspacePreview && (successfulMutation ||
        (completedExecution?.result && !toolCallFailed(completedExecution) &&
          !isLiteralEcho(completedCommand)))) {
      // Shell commands and patches need not declare all affected files.
      // Preserve the immutable host snapshot, but require fresh publication
      // before presenting the potentially changed workspace as current.
      state.workspacePreviewVerifiedDirectory = state.workspacePreview.relativeDirectory;
      state.workspacePreview = undefined;
      sessionPreviews.delete(state.currentSessionId);
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
    if (completedEditPath) state.successfulEditPaths.add(completedEditPath);
    const completedVisualMutationPath = completedEditPath ?? completedWritePath;
    const previousVisualDirectory = sessionPreviews.get(state.currentSessionId)?.relativeDirectory;
    const updatesPublishedProject = previousVisualDirectory && completedVisualMutationPath?.startsWith(`${previousVisualDirectory}/`);
    if (state.ownerIntentObserved && !state.workspacePreviewForbidden &&
        !state.operationsRequired && !state.exactDownloadRequested &&
        completedVisualMutationPath && (updatesPublishedProject || /\.(?:html?|svg|jsx|tsx|vue|svelte)$/i.test(completedVisualMutationPath)) &&
        !/(?:^|\/)(?:node_modules|vendor|__tests__|tests?|fixtures?)(?:\/|$)/i.test(completedVisualMutationPath)) {
      // Actual successful workspace mutations are a stronger delivery signal
      // than a fixed vocabulary of request verbs or languages. Reads, failed
      // tools, quoted model claims and text-only files never activate this.
      state.workspaceVisualArtifactProduced = true;
      state.workspacePreviewRequired = true;
      state.workspaceTaskRequested = true;
      state.workspaceMutationRequested = true;
      if (updatesPublishedProject) state.workspacePreviewDirectory = previousVisualDirectory;
    }
    if (
      completedVisualMutationPath &&
      state.workspaceVisualContinuationRequested &&
      completedVisualMutationPath.startsWith(`${state.workspaceTaskDirectory}/`)
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
          ? toolSearchSelectedToolEvent(event, "read", "core") ??
            toolSearchSandboxMissingRead(event, pendingToolRun, runId)
          : undefined;
    const completedReadPath = completedRead && !toolCallFailed(completedRead)
      ? normalizeWorkspaceFilePath(completedRead.params?.path)
      : undefined;
    if (completedReadPath) {
      state.successfulReadPaths.add(completedReadPath);
    }
    /* Missing-file recovery: when a read of a path we previously wrote fails
     * with a structurally matched ENOENT/no-such-file error, invalidate only
     * that path's stale write-content / compare-swap / repeated-write state
     * so recreating it (even with identical original bytes) is allowed.
     * Do not invalidate on unrelated/ambiguous read errors. */
    const failedRead =
      completedRead && toolCallFailed(completedRead)
        ? completedRead
        : undefined;
    if (failedRead) {
      const readPath = normalizeWorkspaceFilePath(failedRead.params?.path);
      const result = failedRead.result;
      const details = result?.details;
      const missingDetails = details && typeof details === "object" &&
        !Array.isArray(details) ? details : undefined;
      const detailsPathMatches = missingDetails?.path === undefined ||
        normalizeWorkspaceFilePath(missingDetails.path) === readPath;
      const hasStructuredEnoent = missingDetails?.code === "ENOENT" &&
        detailsPathMatches;
      // A read can fail for a dependency rather than the requested file.
      // Match the whole error and its path, never a filename substring.
      const hasTextMissingFileEvidence =
        (!missingDetails?.code || missingDetails.code === "ENOENT") &&
        detailsPathMatches && Array.isArray(result?.content) &&
        result.content.some((block) => {
          if (block?.type !== "text" || typeof block.text !== "string") return false;
          const text = block.text.trim();
          const match = text.match(/^(?:Error:\s*)?ENOENT:\s*no such file or directory, (?:open|stat|lstat) ['"]([^'"\r\n]+)['"]$/i) ||
            text.match(/^Error:\s*(?:File not found|No such file or directory): (.+)$/i);
          return Boolean(match && normalizeWorkspaceFilePath(match[1]) === readPath);
        });
      if (
        readPath &&
        (hasStructuredEnoent || hasTextMissingFileEvidence) &&
        state.successfulWritePaths.has(readPath)
      ) {
        state.successfulWriteContentByPath.delete(readPath);
        state.repeatedWriteBlocks.delete(readPath);
        state.compareSwapRepairCounts.delete(readPath);
      }
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
      const preview = state.ownerIntentObserved && !state.workspacePreviewForbidden && workspacePreviewOutcome(
        previewEvent,
        state.workspacePreviewDirectory,
        state
      );
      if (preview) {
        state.workspacePreviewDirectory = preview.relativeDirectory;
        state.workspacePreviewModelAuthored = workspacePreviewAuthorshipMatches(state, preview);
        state.workspacePreview = preview;
        state.workspaceLastVerifiedPreview = Object.freeze({ ...preview });
        state.successfulWriteContentByPath.clear();
        rememberSessionPreview(state.currentSessionId, preview);
      }
    }
    if (state.operationsRequired || state.hostObservationUsed) {
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
        if (hostObservation.outcome) {
          state.operationsTerminalJobs.set(
            hostObservation.outcome.jobId,
            hostObservation.outcome
          );
        }
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
      const extensionEvent = toolName === EXTENSION_READ_TOOL ? event
        : toolName === "tool_call" ? toolSearchSelectedToolEvent(event, EXTENSION_READ_TOOL, "pixel-ods") : undefined;
      const extensionObservation = extensionEvent ? synchronousExtensionObservation(extensionEvent) : undefined;
      if (extensionObservation) {
        state.operationsSubmittedJobs.set(extensionObservation.submission.jobId, extensionObservation.submission);
        if (extensionObservation.outcome) {
          state.operationsTerminalJobs.set(extensionObservation.outcome.jobId, extensionObservation.outcome);
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
      // A terminal broker receipt completes that operation, not the owner's
      // whole turn. Let the model continue sandbox work and produce its reply.
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
      // Approval may finish between user turns. Keep only actual broker
      // handles in this process-local, session-bound cache. User/tool text
      // cannot seed it; restart recovery still needs durable trusted receipts.
      // This grants job inspection/cancellation, never approval or promotion.
      const submittedJobId = submittedDownloadJobId(exactDownloadEvent);
      if (submittedJobId) {
        rememberSessionDownload(context?.sessionId ?? state.currentSessionId, submittedJobId);
      }
      // Explicit exact-byte requests retain their owner-bound URL/digest.
      // General research may select a source archive or dependency URL. Only
      // an actual matched broker submission creates permission to inspect or
      // cancel its job; tool text and invented job IDs never do.
      if (state.exactDownloadRequested) {
        const submission = exactDownloadSubmission(exactDownloadEvent, state.exactDownloadRequest);
        if (submission) {
          state.exactDownloadSubmissions.set(submission.jobId, submission);
          state.exactDownloadTerminalBlocks = 0;
        }
      } else {
        // The broker may accept a request and subsequently reject its inputs.
        // Let the model read that failure and repair the request. Tracking a
        // real submission grants only job status/cancellation, not artifact
        // validity, publication, or the exclusive exact-byte delivery flow.
        const jobId = submittedDownloadJobId(exactDownloadEvent);
        if (jobId) state.researchDownloadSubmissions.set(jobId, true);
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
      state.githubCanonicalUrl && toolName === "web_fetch" &&
      canonicalGitHubSourceMatches(canonicalFetchUrl(event), state.githubCanonicalUrl) &&
      canonicalWebFetchSucceeded(event)
    ) {
      // A README, repository page, raw source, or repository API response can
      // establish a successful source read. A later failure cannot erase it.
      state.githubCanonicalSatisfied = true;
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
    // An empty or nonterminal receipt is not evidence of a successful test.
    if (verificationFingerprint && !execFailed(execEvent) &&
        !Number.isInteger(execEvent?.result?.details?.exitCode)) return;
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
    if (extensionDiscoveryActive(state)) return undefined;
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
    const requiredHostActions = (requiredHostObservationActions(state) ?? []).filter((action) =>
      !operationsHostEvidenceText(new Set([action]), state.operationsTerminalJobs));
    const hostObservationPending = [...state.operationsSubmittedJobs.keys()].some((jobId) =>
      !state.operationsTerminalJobs.has(jobId));
    if (requiredHostActions.length > 0 && !hostObservationPending) {
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
      !state?.workspacePreviewRequired ||
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
          ? `The published snapshot is verified. Complete the requested read of ${nextPath} and any remaining owner-requested checks before replying.`
          : `The published snapshot is verified. Complete the requested unread static files inside ${state.workspacePreview.relativeDirectory} and any remaining owner-requested checks before replying.`,
      };
    }
    const directory = workspacePreviewDirectoryFromState(state);
    if (!directory) {
      return {
        stage: "workspace-preview-files",
        instruction:
          "Do not reply yet. Deliver the visual project in Workbench: prepare a browser-ready version in one workspace-relative directory with index.html and its local CSS, JavaScript, SVG and image assets. Preserve the project source files. Raw JSX/TSX/Vue/Svelte source is not a browser preview: prepare the runnable output first and inspect its entry point. For a standalone visual asset, create an index.html that displays it. Do not start a server or claim an unverified preview. After index.html has been written or read in this response, call pixel_ods_workspace_preview with that relative directory.",
      };
    }
    state.workspacePreviewDirectory = directory;
    return {
      // A publication followed by a write/check needs a new host receipt.
      // Do not spend that recovery on the initial publication's retry key.
      // Keep one fixed refresh key per run, not an unbounded mutation counter.
      stage: state.workspacePreviewVerifiedDirectory ? "workspace-preview-refresh" : "workspace-preview",
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
    // Native loop blocks can bypass before/after_tool_call entirely. Count
    // their persisted error receipt too; call IDs prevent double accounting.
    if (state && message.isError === true) {
      state.progressBudget.observeResult({callId: toolCallId, tool: message.toolName,
        failed: true});
    }
    if (state?.progressBudget.exhausted) {
      return {message: {...message, content: [{type: 'text', text: RUN_PROGRESS_STOP_REASON}]}};
    }
    const compactWebResult = pending?.transport === "tool_call" &&
      ["web_search", "web_fetch"].includes(pending.selectedToolName) &&
      (!context?.runId || context.runId === pending.runId) &&
      (!event?.runId || event.runId === pending.runId)
      ? projectWebResult(message, pending.capturedToolSearchEnvelope)
      : undefined;
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
          "[ODS assistant next step] Call tool_call next with id openclaw:core:write and " +
          `args path ${JSON.stringify(nextPath)} plus the complete requested content. ` +
          "Keep it concise (under 1000 characters when the requirements fit); do not " +
          `inspect or narrate first.${testFileHint}`
        );
      }
      if (state.latestVerificationStatus === "failed") {
        return `[ODS assistant next step] ${FAILED_TEST_READ_REPAIR_REASON}`;
      }
      if (state.latestVerificationStatus === "passed") {
        return (
          "[ODS assistant next step] Verification passed. Give the owner the concise final " +
          "result now; do not call another tool."
        );
      }
      if (state.workspaceMutationRequested && state.workspaceRequestedFiles.length > 0) {
        return (
          "[ODS assistant next step] All explicitly requested files are written. Run the " +
          "owner-requested verification command now; the project workdir is applied automatically."
        );
      }
      return undefined;
    })();
    const previewStageInstruction = (() => {
      if (state?.workspacePreviewRequired && !state.workspacePreview && !state.workspacePreviewVerifiedDirectory &&
          !state.workspacePreviewForbidden && !state.operationsRequired && !state.exactDownloadRequested) {
        const directory = workspacePreviewDirectoryFromState(state);
        return "[ODS assistant next step] This visual project must be delivered in Workbench. " +
          "Finish all requested files, edits and checks first, then publish BEFORE your final answer. " +
          (directory ? `Call tool_call with id ${WORKSPACE_PREVIEW_TOOL} and args ${JSON.stringify({relativeDirectory:directory})}. ` :
            "Prepare a browser-ready directory with index.html and local assets, preserve the source files, then call pixel_ods_workspace_preview with that relativeDirectory. ") +
          "Do not start a server. A saved file or a previous snapshot is not a verified current preview.";
      }
      if (state?.workspacePreviewVerifiedDirectory && !state.workspacePreview &&
          state.workspacePreviewRequired && !state.workspacePreviewForbidden &&
          !state.operationsRequired && !state.exactDownloadRequested) {
        return "[ODS assistant next step] Files or checks changed after the earlier publication. " +
          "Finish any remaining requested edits and checks, then call tool_call with id " +
          WORKSPACE_PREVIEW_TOOL + " and args " +
          JSON.stringify({ relativeDirectory: state.workspacePreviewVerifiedDirectory }) +
          ". Publish last, after documentation too. Do not claim the earlier snapshot is current.";
      }
      if (
        !state?.workspacePreview ||
        state.operationsRequired ||
        state.exactDownloadRequested
      ) {
        return undefined;
      }
      if (workspacePreviewReadbackComplete(state)) {
        return `[ODS assistant next step] ${WORKSPACE_PREVIEW_COMPLETE_REASON}`;
      }
      const nextPath = workspacePreviewNextKnownReadPath(state);
      return nextPath
        ? (
          "[ODS assistant next step] The preview is already independently verified. " +
          `The owner still requested reading ${nextPath}. ` +
          "Complete that inspection alongside any remaining requested checks."
        )
        : `[ODS assistant next step] ${WORKSPACE_PREVIEW_REQUIRES_READBACK_REASON}`;
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
      !compactWebResult &&
      !previewStageInstruction
    ) {
      return undefined;
    }
    const compactMessage = compactVerification ?? compactCoreResult ?? compactWebResult ?? message;
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
    if (state?.recursiveDeleteDenied || state?.progressBudget.exhausted) return undefined;
    const continuation =
      trustedOperationsContinuation(state, runId) ??
      trustedWorkspacePreviewContinuation(state);
    if (!continuation) return undefined;
    return {
      action: "revise",
      reason: "The assistant has not completed every owner-requested verified step.",
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
    if (state.recursiveDeleteDenied) {
      return { status: "failed", text: RECURSIVE_DELETE_REQUIRES_OWNER_REASON };
    }
    if (state.unrequestedOperationsAborted) {
      return { status: "failed", text: UNREQUESTED_OPERATIONS_LOOP_ABORT_REASON };
    }
    // Successful host facts or a published download cannot hide a failed or
    // still-running workspace verification. The broker receipts stay recorded.
    if ((state.operationsRequired || state.exactDownloadPromotion) && state.latestVerificationStatus === "failed") {
      return { status: "failed", text: VERIFICATION_FAILED_DELIVERY_PREFIX };
    }
    if ((state.operationsRequired || state.exactDownloadPromotion) && state.latestVerificationStatus === "pending") {
      return { status: "pending", text: VERIFICATION_PENDING_DELIVERY_PREFIX };
    }
    if (
      (state.workspacePreviewRequired || state.workspacePreviewAttempted) &&
      !state.operationsRequired &&
      !state.exactDownloadRequested
    ) {
      if (!state.workspacePreview) {
        // A command may have changed the workspace, but it cannot change the
        // immutable host publication. Retain its usable link without treating
        // it as verification of the latest workspace or a completed request.
        if (state.workspaceLastVerifiedPreview) {
          const preview = state.workspaceLastVerifiedPreview;
          return {
            status: "failed",
            text:
              "Your last published preview is still available.\n\n" +
              `[Open last published preview](${preview.url})\n\n` +
              "The workspace has not been verified again since later tool activity. " +
              "This snapshot may not include subsequent changes; publish again to verify the current files.",
            preview: {
              schemaVersion: 1,
              kind: "ods-pixel-workspace-preview",
              ...preview,
            },
          };
        }
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
          `${WORKSPACE_PREVIEW_PUBLISHED_DELIVERY_PREFIX}\n\n` +
          `[Open preview](${state.workspacePreview.url})\n\n` +
          (state.workspacePreviewModelAuthored
            ? "Created by the assistant."
            : "Published from your workspace."),
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
      if (extensionDiscoveryActive(state)) return extensionDiscoveryVerification(state);
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
          ...(!state.toolExecutionAttempted ? { code: OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE } : {}),
        };
      }
      if (
        [...state.operationsSubmittedJobs.keys()].some(
          (jobId) => !state.operationsTerminalJobs.has(jobId)
        )
      ) {
        return { status: "failed", text: OPERATIONS_UNVERIFIED_DELIVERY_PREFIX };
      }
      const mixedVerification = mixedHostInventoryVerification(state);
      if (mixedVerification) return mixedVerification;
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
              ? `- Workspace artifact: The assistant wrote and read back \`/workspace/${state.operationsWorkspaceExpectedPath}\` in this response.`
              : "- Workspace continuation: the requested workspace artifact was not both written and read back successfully in this response."
          }`
          : evidenceText;
        return {
          status:
            !state.operationsNetworkDiscoveryRequested && (
            evidenceText.startsWith(OPERATIONS_HOST_EVIDENCE_PREFIX) ||
            evidenceText.startsWith(OPERATIONS_HOST_COMMAND_EVIDENCE_PREFIX) ||
            evidenceText.startsWith(OPERATIONS_EXTENSION_CATALOG_EVIDENCE_PREFIX) ||
            evidenceText.startsWith(OPERATIONS_EXTENSION_INVENTORY_EVIDENCE_PREFIX) ||
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
            text: "The assistant stopped repeating completed work before it could finish its explanation. " +
              "The following results were recorded by its tools:\n" +
              writtenFiles.slice(0, 20).map((file) => `- File written: \`/workspace/${file}\`.`).join("\n") +
              (writtenFiles.length > 20 ? `\n- ${writtenFiles.length - 20} additional files were written.` : "") +
              "\n- The latest recognized test command completed successfully.\n" +
              "This does not establish complete test coverage or completion of every requested step. " +
              "The workspace is preserved; ask the assistant to continue from these files.",
            ...staleExecWarningSuppression,
          };
        }
      }
      return { status: "passed", ...staleExecWarningSuppression };
    }
    return { status: "none", ...staleExecWarningSuppression };
  }

  function deliveryVerificationForRun(runId) {
    const verification = verificationForRun(runId);
    const state = runs.get(runId);
    if (state?.progressBudget.exhausted) {
      const preview = state.workspacePreview ?? state.workspaceLastVerifiedPreview;
      return {status: 'failed', text: RUN_PROGRESS_STOP_REASON + (preview
        ? `\n\n[Open last published preview](${preview.url})\n\nThis is the last verified publication, not proof that all requested work completed.` : ''),
        ...(preview ? {preview: {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', ...preview}} : {})};
    }
    // An acknowledged harness abort can end the model without a final token.
    // Preserve existing artifact/evidence delivery; for an otherwise empty
    // research result, give ingress the actual cause instead of a generic reply.
    if (state?.webLoopAborted && verification.status === "none") {
      return { status: "failed", text: WEB_LOOP_DELIVERY_REASON };
    }
    const readOnlyOperations = state?.operationsRequired &&
      (extensionDiscoveryActive(state) || state.operationsInventoryOnly ||
        (state.operationsRequiredActions.size > 0 &&
          [...state.operationsRequiredActions].every((action) =>
            action.startsWith("host.") || action === "ods.extensions.list" || action === "ods.extensions.search")));
    return verification.status === "passed" && verification.text &&
      (readOnlyOperations || verification.preview || state?.exactDownloadPromotion)
      ? { ...verification, deliveryMode: "append" }
      : verification;
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
        reason: "The assistant delivers one terminal owner-visible reply per turn.",
      };
    }
    const verification = deliveryVerificationForRun(event?.runId);
    const authoritativeText = verification.text;
    if (!authoritativeText) return undefined;
    if (verification.deliveryMode === "append" &&
        typeof event.payload?.text === "string" && event.payload.text.trim()) {
      const scope = verification.preview
        ? "Publication scope: this receipt verifies the published snapshot, not functional behavior or completion of other requested work."
        : state.exactDownloadPromotion
          ? "Download scope: this receipt verifies bytes at publication, not later edits, analysis accuracy, or completion of other requested work."
          : "Receipt scope: the Operations evidence above does not establish completion of other requested work.";
      const evidence = `${authoritativeText}\n${scope}`;
      return {
        payload: { ...(event.payload ?? {}), text: event.payload.text.endsWith(evidence)
          ? event.payload.text : `${event.payload.text}\n\n${evidence}` },
        reason: "Preserve the model's task reply alongside separately scoped evidence.",
      };
    }
    return {
      payload: {
        ...(event.payload ?? {}),
        text: authoritativeText,
      },
      reason:
        "The assistant replaced an unverified terminal reply with host-authoritative evidence truth.",
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
    deliveryVerificationForRun,
    verificationStatus: (runId) => runs.get(runId)?.latestVerificationStatus,
    trackedRunCount: () => runs.size,
    trackedUserCount: () => activeUsers.size,
    observeModelEnd,
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
