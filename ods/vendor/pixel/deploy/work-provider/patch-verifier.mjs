import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

export class WorkProviderPatchVerifierError extends Error {}
export class WorkProviderPatchInputError extends WorkProviderPatchVerifierError {}
function verifierFail(message) { throw new WorkProviderPatchVerifierError(message); }
function inputFail(message) { throw new WorkProviderPatchInputError(message); }

function normalizePatch(value) {
  if (typeof value !== "string") return value;
  let patch = value.replaceAll("\r\n", "\n").trim();
  const fenced = /^```(?:diff|patch)?\n([\s\S]*?)\n```$/u.exec(patch);
  if (fenced) patch = fenced[1].trim();
  if (!patch.startsWith("diff --git ") && patch.startsWith("--- a/fixture.mjs\n+++ b/fixture.mjs\n")) {
    patch = `diff --git a/fixture.mjs b/fixture.mjs\n${patch}`;
  }
  return patch.endsWith("\n") ? patch : `${patch}\n`;
}

function runGitApply(root, patch, args) {
  return new Promise((resolve, reject) => {
    const child = spawn("git", ["-c", "core.autocrlf=false", "-c", "core.safecrlf=true", "apply", ...args, "-"], {
      cwd: root,
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "ignore", "ignore"],
    });
    child.once("error", () => reject(new WorkProviderPatchVerifierError("disposable patch verifier could not start git apply")));
    child.once("close", (code, signal) => resolve(code === 0 && signal === null));
    child.stdin.end(patch, "utf8");
  });
}

function validateInputs(patch, base, expected) {
  for (const [value, label] of [[base, "base"], [expected, "expected"]]) {
    if (typeof value !== "string" || value.length < 1 || Buffer.byteLength(value, "utf8") > 64 * 1024 || value.includes("\u0000")) verifierFail(`disposable trusted ${label} is invalid`);
  }
  if (typeof patch !== "string" || patch.length < 1 || Buffer.byteLength(patch, "utf8") > 64 * 1024 || patch.includes("\u0000")) inputFail("disposable untrusted patch is invalid");
  const headers = [...patch.matchAll(/^diff --git a\/(\S+) b\/(\S+)$/gmu)];
  if (headers.length !== 1 || headers[0][1] !== "fixture.mjs" || headers[0][2] !== "fixture.mjs") inputFail("disposable patch may modify only fixture.mjs");
  if (!/^--- a\/fixture\.mjs$/mu.test(patch) || !/^\+\+\+ b\/fixture\.mjs$/mu.test(patch)
    || /^(?:new file mode|deleted file mode|rename from|rename to|copy from|copy to|GIT binary patch|Binary files)/mu.test(patch)) {
    inputFail("disposable patch has unsupported file semantics");
  }
  const lines = patch.split("\n");
  if (lines.at(-1) === "") lines.pop();
  let inHunk = false;
  for (const line of lines.slice(3)) {
    if (line.startsWith("@@ ")) { inHunk = true; continue; }
    if (!inHunk || ![" ", "+", "-", "\\"].includes(line[0])) inputFail("disposable patch contains text outside its unified diff hunks");
  }
  if (!inHunk) inputFail("disposable patch has no unified diff hunk");
}

// Applies an untrusted model patch only to a fresh one-file disposable directory.
// No shell is involved, the path is fixed, and the directory is removed in all cases.
export async function verifyDisposablePatch({ patch, base, expected }) {
  const normalized = normalizePatch(patch);
  validateInputs(normalized, base, expected);
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-patch-"));
  try {
    await writeFile(join(root, "fixture.mjs"), base, { encoding: "utf8", flag: "wx" });
    const checkPassed = await runGitApply(root, normalized, ["--check", "--whitespace=error-all"]);
    const applied = checkPassed && await runGitApply(root, normalized, ["--whitespace=error-all"]);
    const exact = applied && await readFile(join(root, "fixture.mjs"), "utf8") === expected;
    const secondApplyRejected = applied && !(await runGitApply(root, normalized, ["--check", "--whitespace=error-all"]));
    const reverseCheckPassed = applied && await runGitApply(root, normalized, ["--check", "--reverse", "--whitespace=error-all"]);
    return Object.freeze({ checkPassed, applied, exact, secondApplyRejected, reverseCheckPassed });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}
