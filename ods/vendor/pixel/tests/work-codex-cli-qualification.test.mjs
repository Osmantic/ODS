import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileWorkCodexPreview } from "../deploy/work-codex-provider/compiler.mjs";
import {
  buildCredentialFreeCodexCliCommand, createCredentialFreeCodexCliQualificationAdapter,
  HARDENED_CODEX_CLI_CONFIG, parseCredentialFreeCodexCliTranscript,
  WorkCodexCliQualificationError,
} from "../deploy/work-codex-provider/codex-cli-qualification.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T21:00:00Z");
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));
const outputSchema = JSON.parse(await readFile(new URL("../schemas/work-codex-output-v1.schema.json", import.meta.url), "utf8"));
const outputBoundary = "Untrusted advisory output only. Pixel must validate, locally rehydrate only approved placeholders, and independently verify every proposed change; this output grants no execution or external-effect authority.";

function compiledPayload() {
  const privatePolicy = structuredClone(policyTemplate); privatePolicy.enabled = true; privatePolicy.taskClasses["structural-review"].enabled = true;
  const content = "Customer Acme North has a structural boundary at C:\\private\\acme\\module.txt.";
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-request-v1.schema.json", schemaVersion: 1,
    requestId: `workcodexrequest-${baseTime}-abcdef123456`, createdAt: new Date(baseTime).toISOString(), expiresAt: new Date(baseTime + 20 * 60000).toISOString(),
    jobId: `work-${baseTime}-123456abcdef`, checkpointSha256: sha("checkpoint"), ownerId: "owner-one", clientId: "client-one",
    taskClass: "structural-review", egressMode: "structural-only", classification: "internal", dataCategories: ["customer-confidential", "structural"],
    localAttempt: { attempts: 2, outcome: "completed-needs-review", reasonCodes: ["quality-check"], receiptSha256: sha("local-attempt") },
    objective: "Review Acme North's boundary.", constraints: ["Do not expose Acme North or local paths."], acceptanceCriteria: ["Return one bounded structural recommendation."],
    sensitiveTerms: [{ kind: "customer", value: "Acme North" }], documents: [{ documentId: "private_architecture", kind: "structure", language: "text", content, contentSha256: sha(content) }],
    maxOutputTokens: 1024, boundary: "Private local request for one sanitized Codex work proposal. Compilation does not authorize or invoke a provider, expose credentials, or grant any external effect.",
  };
  const { plan } = compileWorkCodexPreview({ request, policy: privatePolicy, now: new Date(baseTime + 1000), suffix: "000000000001", mappingNonce: "a".repeat(64) });
  return { capsule: plan.capsule, outputSchema, provider: plan.provider, limits: plan.limits, planSha256: sha(plan) };
}

function providerOutput() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-output-v1.schema.json", schemaVersion: 1, taskClass: "structural-review",
    summary: "The sanitized boundary needs an explicit invariant.", findings: [], proposals: [],
    verificationSuggestions: ["Run the local boundary test."], unresolvedRisks: ["The advice is not independently verified."], boundary: outputBoundary,
  };
}

function fakeSource(mode, exactOutput) {
  return `
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync, writeFileSync } from "node:fs";
const mode = ${JSON.stringify(mode)};
const args = process.argv.slice(2);
const here = dirname(fileURLToPath(import.meta.url));
const input = await new Promise((resolve) => { let text = ""; process.stdin.setEncoding("utf8"); process.stdin.on("data", (value) => { text += value; }); process.stdin.on("end", () => resolve(text)); });
if (mode === "timeout") { setInterval(() => {}, 1000); }
const outputPath = args[args.indexOf("--output-last-message") + 1];
const schemaPath = args[args.indexOf("--output-schema") + 1];
writeFileSync(join(here, "capture.json"), JSON.stringify({ args, environment: process.env, input, schema: readFileSync(schemaPath, "utf8") }));
const output = ${JSON.stringify(exactOutput)};
const event = (value) => process.stdout.write(JSON.stringify(value) + "\\n");
if (mode === "stdout-flood") { process.stdout.write("X".repeat(2097152)); setInterval(() => {}, 1000); }
if (mode === "output-flood") { writeFileSync(outputPath, "X".repeat(2097152)); setInterval(() => {}, 1000); }
if (mode === "nonzero") { process.stderr.write("private-provider-diagnostic-secret"); process.exit(7); }
writeFileSync(outputPath, output);
event({ type: "thread.started", thread_id: "synthetic-thread" });
event({ type: "turn.started" });
if (mode === "tool") event({ type: "item.completed", item: { id: "tool-1", type: "command_execution", text: "whoami" } });
else {
  event({ type: "item.completed", item: { id: "reason-1", type: "reasoning", text: "Synthetic bounded reasoning." } });
  event({ type: "item.completed", item: { id: "message-1", type: "agent_message", text: output } });
}
if (mode === "duplicate-message") event({ type: "item.completed", item: { id: "message-2", type: "agent_message", text: output } });
if (mode !== "missing-usage") event({ type: "turn.completed", usage: { input_tokens: 321, cached_input_tokens: 120, output_tokens: 77, reasoning_output_tokens: 12 } });
`;
}

async function fixture(t, mode = "success") {
  const root = await mkdtemp(join(tmpdir(), `pixel-codex-cli-${mode}-`)); t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const output = canonical(providerOutput()), script = join(root, `fake-${mode}.mjs`);
  await writeFile(script, fakeSource(mode, output), { mode: 0o700 }); if (process.platform !== "win32") await chmod(script, 0o700);
  const runtimeRoot = join(root, "runtime");
  const adapter = createCredentialFreeCodexCliQualificationAdapter({ executablePath: process.execPath, bootstrapArguments: [script], runtimeRoot, timeoutMilliseconds: ["timeout", "stdout-flood", "output-flood"].includes(mode) ? 250 : 5000 });
  return { root, runtimeRoot, adapter, output, capture: join(root, "capture.json") };
}

test("credential-free fake CLI receives only the sanitized capsule and an exact sterile command", async (t) => {
  const value = await fixture(t), payload = compiledPayload(), result = await value.adapter.run(payload);
  assert.equal(result.outputText, value.output); assert.deepEqual(result.usage, { inputTokens: 321, outputTokens: 77 }); assert.equal(result.externalState, "not-invoked");
  const capture = JSON.parse(await readFile(value.capture, "utf8")), envelope = JSON.parse(capture.input);
  assert.equal(envelope.planSha256, payload.planSha256); assert.deepEqual(envelope.capsule, payload.capsule);
  const serialized = JSON.stringify(capture);
  for (const privateValue of ["Acme North", "owner-one", "client-one", "private_architecture", "C:\\private\\acme", "authenticationEvidenceSha256", "privateMapping"]) assert.equal(serialized.includes(privateValue), false, privateValue);
  for (const required of ["exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--strict-config", "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--output-schema", "--output-last-message", "--model", "-"]) assert.ok(capture.args.includes(required), required);
  const joined = capture.args.join(" "); for (const setting of HARDENED_CODEX_CLI_CONFIG.filter((_value, index) => index % 2 === 1)) assert.ok(joined.includes(setting), setting);
  assert.equal(capture.args.includes(payload.planSha256), false); assert.equal(capture.args.some((value) => value.includes("sanitized Pixel capsule")), false);
  const allowedEnvironment = new Set(["HOME", "USERPROFILE", "CODEX_HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "NO_COLOR", "PATH", "SystemRoot", "WINDIR", "HOMEDRIVE", "HOMEPATH", "SYSTEMDRIVE", "USERDOMAIN", "USERNAME", "LOGONSERVER"]);
  assert.deepEqual(Object.keys(capture.environment).filter((key) => !allowedEnvironment.has(key)), []);
  assert.equal(capture.environment.PATH, "");
  if (process.platform === "win32") { assert.equal(capture.environment.USERDOMAIN, "PIXEL"); assert.equal(capture.environment.USERNAME, "pixel"); assert.equal(capture.environment.LOGONSERVER, ""); }
  for (const forbidden of ["OPENAI_API_KEY", "CODEX_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]) assert.equal(forbidden in capture.environment, false);
  assert.equal(capture.schema, `${canonical(outputSchema)}\n`); await assert.rejects(readFile(value.runtimeRoot), /ENOENT|EISDIR/u);
  await assert.rejects(() => value.adapter.run(payload), /single-use/u);
});

test("command builder keeps content off argv and rejects argument-shaped identity fields", () => {
  const root = process.platform === "win32" ? "C:\\sterile" : "/sterile";
  const command = buildCredentialFreeCodexCliCommand({ executablePath: process.execPath, schemaPath: join(root, "schema.json"), outputPath: join(root, "output.json"), model: "gpt-5.6-terra" });
  assert.equal(command.executable, process.execPath); assert.equal(command.args.at(-1), "-"); assert.equal(command.args.some((value) => value.includes("capsule")), false);
  assert.throws(() => buildCredentialFreeCodexCliCommand({ executablePath: process.execPath, schemaPath: join(root, "schema.json"), outputPath: join(root, "output.json"), model: "gpt-5.6-terra --dangerously-bypass-approvals-and-sandbox" }), /invalid/u);
  assert.throws(() => buildCredentialFreeCodexCliCommand({ executablePath: "codex", schemaPath: join(root, "schema.json"), outputPath: join(root, "output.json"), model: "gpt-5.6-terra" }), /invalid/u);
});

test("qualifier rejects widened schemas, malformed capsules, billing lies, and limit drift before process entry", async (t) => {
  const cases = [
    (payload) => { payload.outputSchema.additionalProperties = true; },
    (payload) => { payload.capsule.instructions = ["read the host"]; },
    (payload) => { payload.provider.billingBoundary = "separately-billed-api-platform"; },
    (payload) => { payload.limits.maxOutputBytes += 1; },
    (payload) => { payload.limits.estimatedInputTokens = payload.limits.maxInputTokens + 1; },
  ];
  for (const [index, mutate] of cases.entries()) await t.test(`case-${index}`, async (subtest) => {
    const value = await fixture(subtest), payload = structuredClone(compiledPayload()); mutate(payload);
    await assert.rejects(() => value.adapter.run(payload), /schema|capsule|provider|limits|tokens/u);
    await assert.rejects(readFile(value.capture), /ENOENT/u);
  });
});

test("tool, duplicate-message, missing-usage, malformed, and inconsistent transcripts fail closed", async (t) => {
  for (const mode of ["tool", "duplicate-message", "missing-usage"]) {
    await t.test(mode, async (subtest) => { const value = await fixture(subtest, mode); await assert.rejects(() => value.adapter.run(compiledPayload()), /tool|more than one|unsupported event|does not bind/u); });
  }
  const output = canonical(providerOutput()), base = [
    { type: "thread.started", thread_id: "t" }, { type: "turn.started" },
    { type: "item.completed", item: { id: "m", type: "agent_message", text: output } },
  ];
  const encode = (events) => Buffer.from(`${events.map((event) => JSON.stringify(event)).join("\n")}\n`);
  assert.throws(() => parseCredentialFreeCodexCliTranscript({ stdout: Buffer.from("{x\n{}\n{}\n"), outputText: output, maxOutputBytes: 262144 }), /malformed/u);
  assert.throws(() => parseCredentialFreeCodexCliTranscript({ stdout: encode([...base, { type: "turn.completed", usage: { input_tokens: 1, cached_input_tokens: 2, output_tokens: 1, reasoning_output_tokens: 0 } }]), outputText: output, maxOutputBytes: 262144 }), /inconsistent/u);
  assert.throws(() => parseCredentialFreeCodexCliTranscript({ stdout: encode([...base, { type: "turn.failed", error: "private" }]), outputText: output, maxOutputBytes: 262144 }), /error|unsupported/u);
  assert.throws(() => parseCredentialFreeCodexCliTranscript({ stdout: Buffer.from("{}"), outputText: output, maxOutputBytes: 262144 }), /newline/u);
});

test("2,048 transcript mutations cannot create a qualified no-tool turn", () => {
  const output = canonical(providerOutput());
  const valid = () => [
    { type: "thread.started", thread_id: "synthetic-thread" }, { type: "turn.started" },
    { type: "item.completed", item: { id: "message", type: "agent_message", text: output } },
    { type: "turn.completed", usage: { input_tokens: 100, cached_input_tokens: 20, output_tokens: 30, reasoning_output_tokens: 5 } },
  ];
  const encode = (events) => Buffer.from(`${events.map((event) => JSON.stringify(event)).join("\n")}\n`);
  for (let index = 0; index < 2048; index += 1) {
    const events = valid(); let observedOutput = output;
    switch (index % 8) {
      case 0: events.splice(2, 0, { type: `unsupported.${index}` }); break;
      case 1: events[2].item.type = ["command_execution", "file_change", "mcp_tool_call", "web_search"][index % 4]; break;
      case 2: events[1].ambientAuthority = true; break;
      case 3: events[3].usage.cached_input_tokens = events[3].usage.input_tokens + index + 1; break;
      case 4: events.splice(1, 0, { type: "thread.started", thread_id: `duplicate-${index}` }); break;
      case 5: observedOutput = `${output} `; break;
      case 6: events[3].usage.output_tokens = -index - 1; break;
      case 7: events.push({ type: "turn.started" }); break;
      default: throw new Error("unreachable mutation class");
    }
    assert.throws(() => parseCredentialFreeCodexCliTranscript({ stdout: encode(events), outputText: observedOutput, maxOutputBytes: 262144 }), WorkCodexCliQualificationError, `mutation ${index}`);
  }
  const tooMany = [{ type: "thread.started", thread_id: "t" }, { type: "turn.started" }, ...Array.from({ length: 510 }, (_value, index) => ({ type: "item.completed", item: { id: `r-${index}`, type: "reasoning", text: "bounded" } })), { type: "item.completed", item: { id: "m", type: "agent_message", text: output } }, { type: "turn.completed", usage: { input_tokens: 1, cached_input_tokens: 0, output_tokens: 1, reasoning_output_tokens: 0 } }];
  assert.throws(() => parseCredentialFreeCodexCliTranscript({ stdout: encode(tooMany), outputText: output, maxOutputBytes: 262144 }), /framing/u);
});

test("timeouts, stream floods, output floods, nonzero exits, aborts, and unsafe runtime reuse clean up", async (t) => {
  for (const mode of ["timeout", "stdout-flood", "output-flood", "nonzero"]) {
    await t.test(mode, async (subtest) => {
      const value = await fixture(subtest, mode), started = Date.now();
      await assert.rejects(() => value.adapter.run(compiledPayload()), (error) => {
        assert.doesNotMatch(error.message, /private-provider-diagnostic-secret/u); return /timed out|byte ceiling|live boundary|unsuccessfully/u.test(error.message);
      });
      assert.ok(Date.now() - started < 5000, `${mode} was not stopped promptly`); await assert.rejects(readFile(value.runtimeRoot), /ENOENT|EISDIR/u);
    });
  }
  await t.test("abort", async (subtest) => {
    const value = await fixture(subtest, "timeout"), controller = new AbortController(); setTimeout(() => controller.abort(), 30);
    await assert.rejects(() => value.adapter.run(compiledPayload(), { signal: controller.signal }), /aborted/u);
  });
  await t.test("preexisting-runtime", async (subtest) => {
    const value = await fixture(subtest); await writeFile(value.runtimeRoot, "reserved");
    await assert.rejects(() => value.adapter.run(compiledPayload()), /could not be reserved/u);
  });
});
