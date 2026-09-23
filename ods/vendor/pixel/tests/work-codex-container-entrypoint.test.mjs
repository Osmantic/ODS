import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough, Readable } from "node:stream";
import test from "node:test";

import { compileWorkCodexPreview } from "../deploy/work-codex-provider/compiler.mjs";
import { runWorkCodexContainerEntrypoint } from "../deploy/work-codex-provider/container-entrypoint.mjs";
import { encodeWorkCodexContainerFrame } from "../deploy/work-codex-provider/container-protocol.mjs";
import { HARDENED_CODEX_CLI_CONFIG, parseCredentialFreeCodexCliTranscript } from "../deploy/work-codex-provider/codex-cli-qualification.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-11T00:00:00Z"), sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));
const outputSchema = JSON.parse(await readFile(new URL("../schemas/work-codex-output-v1.schema.json", import.meta.url), "utf8"));
const outputBoundary = "Untrusted advisory output only. Pixel must validate, locally rehydrate only approved placeholders, and independently verify every proposed change; this output grants no execution or external-effect authority.";

function outputObject() { return { $schema: "https://osmantic.com/pixel/schemas/work-codex-output-v1.schema.json", schemaVersion: 1, taskClass: "structural-review", summary: "The sanitized boundary is explicit.", findings: [], proposals: [], verificationSuggestions: ["Run local checks."], unresolvedRisks: ["Not independently verified."], boundary: outputBoundary }; }

function payload(api = false) {
  const policy = structuredClone(policyTemplate); policy.enabled = true; policy.taskClasses["structural-review"].enabled = true;
  if (api) { policy.provider.authMode = "api-key"; policy.transport.allowedHosts = ["api.openai.com"]; policy.credentialCustody = { mode: "broker-private-api-key-file", credentialId: "codex-api-key", maxBytes: 8192, mutableRefresh: false, runtimeAccess: "stdin-only-ephemeral-login", materialProjectedToPixel: false, ambientEnvironment: false }; policy.provider.billing = { mode: "metered", boundary: "Separately billed OpenAI API Platform usage; ChatGPT subscription access does not cover this route.", currency: "USD", inputMicrosPerMillionTokens: 1, outputMicrosPerMillionTokens: 2, source: "https://openai.com/api/pricing/", asOf: "2026-08-10", maxEstimatedCostMicros: 1000000 }; }
  const content = "A structural fixture.", request = { $schema: "https://osmantic.com/pixel/schemas/work-codex-request-v1.schema.json", schemaVersion: 1, requestId: `workcodexrequest-${baseTime}-000000000001`, createdAt: new Date(baseTime).toISOString(), expiresAt: new Date(baseTime + 1200000).toISOString(), jobId: `work-${baseTime}-000000000002`, checkpointSha256: sha("checkpoint"), ownerId: "owner", clientId: "client", taskClass: "structural-review", egressMode: "structural-only", classification: "internal", dataCategories: ["structural"], localAttempt: { attempts: 2, outcome: "completed-needs-review", reasonCodes: ["quality-check"], receiptSha256: sha("attempt") }, objective: "Review the fixture.", constraints: ["Advisory only."], acceptanceCriteria: ["Return advice."], sensitiveTerms: [], documents: [{ documentId: "private", kind: "structure", language: "text", content, contentSha256: sha(content) }], maxOutputTokens: 1024, boundary: "Private local request for one sanitized Codex work proposal. Compilation does not authorize or invoke a provider, expose credentials, or grant any external effect." };
  const { plan } = compileWorkCodexPreview({ request, policy, now: new Date(baseTime + 1000), suffix: "000000000003", mappingNonce: sha("mapping") });
  return { capsule: plan.capsule, outputSchema, provider: plan.provider, limits: plan.limits, planSha256: sha(plan) };
}

function fakeCodexSource({ capturePath, outputText, expectedCredential }) {
  return `
import { readFileSync, writeFileSync } from "node:fs";
const args = process.argv.slice(2), input = await new Promise((resolve) => { const chunks = []; process.stdin.on("data", (chunk) => chunks.push(chunk)); process.stdin.on("end", () => resolve(Buffer.concat(chunks))); });
const current = (() => { try { return JSON.parse(readFileSync(${JSON.stringify(capturePath)}, "utf8")); } catch { return {}; } })();
if (args[0] === "login") { current.login = { args, credentialMatched: input.toString("utf8") === ${JSON.stringify(expectedCredential)} }; writeFileSync(process.env.CODEX_HOME + "/auth.json", JSON.stringify({ auth_mode: "api-key", fixture: "ephemeral" }), { mode: 384 }); writeFileSync(${JSON.stringify(capturePath)}, JSON.stringify(current)); process.exit(0); }
const outputPath = args[args.indexOf("--output-last-message") + 1], schemaPath = args[args.indexOf("--output-schema") + 1];
current.exec = { args, environment: process.env, envelope: JSON.parse(input.toString("utf8")), schema: JSON.parse(readFileSync(schemaPath, "utf8")), authMode: JSON.parse(readFileSync(process.env.CODEX_HOME + "/auth.json", "utf8")).auth_mode };
writeFileSync(${JSON.stringify(capturePath)}, JSON.stringify(current)); const output = ${JSON.stringify(outputText)}; writeFileSync(outputPath, output, { mode: 384 });
const event = (value) => process.stdout.write(JSON.stringify(value) + "\\n"); event({ type: "thread.started", thread_id: "entrypoint-fixture" }); event({ type: "turn.started" }); event({ type: "item.completed", item: { id: "message", type: "agent_message", text: output } }); event({ type: "turn.completed", usage: { input_tokens: 12, cached_input_tokens: 2, output_tokens: 5, reasoning_output_tokens: 1 } });
`;
}

async function runFixture(t, api) {
  const root = await mkdtemp(join(tmpdir(), `pixel-codex-entry-${api ? "api" : "chat"}-`)); t.after(() => rm(root, { recursive: true, force: true })); if (process.platform !== "win32") await chmod(root, 0o700); await mkdir(join(root, "out"), { mode: 0o700 });
  const capturePath = join(root, "capture.json"), script = join(root, "fake-codex.mjs"), credential = api ? "sk-fixture_abcdefghijklmnopqrstuvwxyz123456" : '{"auth_mode":"chatgpt","fixture":"private-material"}', outputText = canonical(outputObject());
  await writeFile(script, fakeCodexSource({ capturePath, outputText, expectedCredential: credential }), { mode: 0o700 }); if (process.platform !== "win32") await chmod(script, 0o700);
  const stdout = new PassThrough(), stderr = new PassThrough(), stdoutChunks = [], stderrChunks = []; stdout.on("data", (chunk) => stdoutChunks.push(chunk)); stderr.on("data", (chunk) => stderrChunks.push(chunk));
  await runWorkCodexContainerEntrypoint({ input: Readable.from(encodeWorkCodexContainerFrame({ payload: payload(api), credential })), executablePath: process.execPath, bootstrapArguments: [script], root, proxyUrl: "http://172.30.8.3:3128", output: stdout, diagnostic: stderr }); stdout.end(); stderr.end();
  return { root, credential, outputText, capture: JSON.parse(await readFile(capturePath, "utf8")), stdout: Buffer.concat(stdoutChunks), stderr: Buffer.concat(stderrChunks) };
}

test("container entrypoint runs ChatGPT work with the hardened no-tool command and emits only a private refresh file", async (t) => {
  const value = await runFixture(t, false); assert.equal(value.capture.exec.authMode, "chatgpt"); assert.equal(value.capture.login, undefined); assert.equal(value.capture.exec.envelope.capsule.objective, "Review the fixture.");
  const joined = value.capture.exec.args.join(" "); for (const setting of HARDENED_CODEX_CLI_CONFIG.filter((_entry, index) => index % 2 === 1)) assert.ok(joined.includes(setting), setting);
  assert.equal(value.capture.exec.environment.HTTPS_PROXY, "http://172.30.8.3:3128"); assert.equal(JSON.stringify(value.capture).includes(value.credential), false); assert.deepEqual(parseCredentialFreeCodexCliTranscript({ stdout: value.stdout, outputText: value.outputText, maxOutputBytes: 262144 }).usage, { inputTokens: 12, outputTokens: 5 });
  assert.match(await readFile(join(value.root, "out", "refreshed-auth.json"), "utf8"), /private-material/u); await assert.rejects(readFile(join(value.root, "codex-home", "auth.json")), /ENOENT/u);
});

test("container entrypoint performs API login over stdin and never places the key in exec argv or environment", async (t) => {
  const value = await runFixture(t, true); assert.equal(value.capture.login.credentialMatched, true); assert.deepEqual(value.capture.login.args, ["login", "--with-api-key"]); assert.equal(value.capture.exec.authMode, "api-key");
  assert.equal(JSON.stringify(value.capture.exec).includes(value.credential), false); assert.equal("OPENAI_API_KEY" in value.capture.exec.environment, false); await assert.rejects(readFile(join(value.root, "out", "refreshed-auth.json")), /ENOENT/u);
});

test("container entrypoint rejects malformed frames before starting Codex", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-codex-entry-bad-")); t.after(() => rm(root, { recursive: true, force: true })); await mkdir(join(root, "out"), { recursive: true }); let started = false;
  await assert.rejects(() => runWorkCodexContainerEntrypoint({ input: Readable.from(Buffer.from("not-a-frame")), executablePath: process.execPath, bootstrapArguments: [], root, proxyUrl: "http://172.30.8.3:3128", output: new PassThrough(), diagnostic: new PassThrough() }), /frame/u); assert.equal(started, false);
});
