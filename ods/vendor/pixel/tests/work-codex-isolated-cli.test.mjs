import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileWorkCodexPreview } from "../deploy/work-codex-provider/compiler.mjs";
import { inspectWorkCodexCredentialCustody, readWorkCodexChatgptAuthCache } from "../deploy/work-codex-provider/credential-custody.mjs";
import { createIsolatedWorkCodexCliAdapter, IsolatedWorkCodexCliError } from "../deploy/work-codex-provider/isolated-codex-cli.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T23:30:00Z"), sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));
const outputSchema = JSON.parse(await readFile(new URL("../schemas/work-codex-output-v1.schema.json", import.meta.url), "utf8"));
const egressBoundary = "Deployment-private Codex egress proxy policy. The worker has only an internal Docker network and can reach only this proxy; the proxy permits CONNECT to the exact reviewed OpenAI host list on port 443 and stores no request or response body.";
const outputBoundary = "Untrusted advisory output only. Pixel must validate, locally rehydrate only approved placeholders, and independently verify every proposed change; this output grants no execution or external-effect authority.";
const ids = { runnerManifest: "a".repeat(64), runnerImage: "b".repeat(64), proxyImage: "c".repeat(64), network: "d".repeat(64), proxy: "e".repeat(64) };

function providerOutput() { return { $schema: "https://osmantic.com/pixel/schemas/work-codex-output-v1.schema.json", schemaVersion: 1, taskClass: "structural-review", summary: "The sanitized fixture needs one invariant.", findings: [], proposals: [], verificationSuggestions: ["Run the local test."], unresolvedRisks: ["Advice remains unverified."], boundary: outputBoundary }; }

function fakeDockerSource({ mode, output, credential, policy, runtime, captureRoot }) {
  return `
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
const args = process.argv.slice(2), mode = ${JSON.stringify(mode)}, captureRoot = ${JSON.stringify(captureRoot)};
const save = (name, value) => writeFileSync(captureRoot + "/" + name + ".json", JSON.stringify(value));
if (args[0] === "image" && args[1] === "inspect") { process.stdout.write(JSON.stringify([{ Id: "sha256:${ids.runnerImage}", RepoDigests: [${JSON.stringify(policy.transport.runnerImageRef)}], Os: "linux", Architecture: "amd64", Config: { Labels: { "org.osmantic.pixel.codex-version": ${JSON.stringify(policy.transport.codexVersion)}, "org.osmantic.pixel.codex-binary-sha256": ${JSON.stringify(policy.transport.codexBinarySha256)}, "org.osmantic.pixel.entrypoint-sha256": ${JSON.stringify(policy.transport.entrypointSha256)} } } }])); process.exit(0); }
if (args[0] === "network" && args[1] === "inspect") {
  const network = { Id: ${JSON.stringify(runtime.networkId)}, Name: ${JSON.stringify(runtime.networkName)}, Driver: "bridge", Internal: true, Attachable: false, Containers: { [${JSON.stringify(runtime.proxyContainerId)}]: { Name: ${JSON.stringify(runtime.proxyName)}, IPv4Address: ${JSON.stringify(`${runtime.proxyIp}/29`)} } } };
  if (mode === "wide-network") network.Internal = false; process.stdout.write(JSON.stringify([network])); process.exit(0);
}
if (args[0] === "container" && args[1] === "inspect") {
  const proxy = { Id: ${JSON.stringify(runtime.proxyContainerId)}, Name: ${JSON.stringify(`/${runtime.proxyName}`)}, Image: "sha256:${ids.proxyImage}", State: { Running: true, Paused: false, Restarting: false }, Config: { User: "65532:65532", Env: [${JSON.stringify(`PIXEL_CODEX_EGRESS_POLICY_SHA256=${policy.transport.proxyPolicySha256}`)}], Labels: { "org.osmantic.pixel.egress-policy-sha256": ${JSON.stringify(policy.transport.proxyPolicySha256)} } }, HostConfig: { Privileged: false, ReadonlyRootfs: true, CapDrop: ["ALL"], SecurityOpt: ["no-new-privileges:true"], NetworkMode: ${JSON.stringify(runtime.externalNetworkName)}, PidsLimit: 64, Memory: 268435456, MemorySwap: 268435456, Devices: [], PortBindings: {} }, Mounts: [{ Type: "bind", Source: ${JSON.stringify(runtime.proxyPolicyPath)}, Destination: "/etc/pixel-egress/policy.json", RW: false }], NetworkSettings: { Networks: { [${JSON.stringify(runtime.networkName)}]: { IPAddress: ${JSON.stringify(runtime.proxyIp)}, Aliases: ["pixel-codex-egress"] }, [${JSON.stringify(runtime.externalNetworkName)}]: { IPAddress: "172.30.9.2", Aliases: [] } } } };
  if (mode === "privileged-proxy") proxy.HostConfig.Privileged = true; process.stdout.write(JSON.stringify([proxy])); process.exit(0);
}
if (args[0] === "rm") { save("cleanup", { args }); process.exit(mode === "cleanup-failure" ? 1 : 0); }
if (args[0] === "ps") { if (mode === "cleanup-failure") process.stdout.write(${JSON.stringify(ids.proxy)} + "\\n"); process.exit(0); }
if (args[0] !== "run") process.exit(91);
const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk); const frame = Buffer.concat(chunks), magic = Buffer.from("PIXEL-CODEX-CONTAINER-V1\\n"), headerEnd = frame.indexOf(10, magic.length), header = JSON.parse(frame.subarray(magic.length, headerEnd)), body = headerEnd + 1;
const observedCredential = frame.subarray(body, body + header.credentialBytes).toString("utf8"), payload = JSON.parse(frame.subarray(body + header.credentialBytes).toString("utf8"));
const mount = args[args.indexOf("--mount") + 1], match = /^type=bind,source=(.*),destination=\\/run\\/pixel\\/out$/.exec(mount), outputRoot = match?.[1]; if (!outputRoot) process.exit(92);
save("run", { args, environmentKeys: Object.keys(process.env).sort(), credentialMatched: observedCredential === ${JSON.stringify(credential)}, payload, frameBytes: frame.length });
if (mode === "timeout") { setInterval(() => {}, 1000); } if (mode === "nonzero") process.exit(7);
mkdirSync(outputRoot, { recursive: true }); const output = ${JSON.stringify(output)}; writeFileSync(outputRoot + "/last-message.json", mode === "output-flood" ? "X".repeat(1048576) : output, { mode: 384 });
writeFileSync(outputRoot + "/refreshed-auth.json", ${JSON.stringify('{"auth_mode":"chatgpt","fixture":"rotated-by-container"}')}, { mode: 384 });
const event = (value) => process.stdout.write(JSON.stringify(value) + "\\n"); event({ type: "thread.started", thread_id: "fixture" }); event({ type: "turn.started" });
if (mode === "tool") event({ type: "item.completed", item: { id: "tool", type: "command_execution", text: "whoami" } }); else event({ type: "item.completed", item: { id: "message", type: "agent_message", text: output } });
event({ type: "turn.completed", usage: { input_tokens: 300, cached_input_tokens: 100, output_tokens: 70, reasoning_output_tokens: 10 } });
`;
}

async function fixture(t, mode = "success", api = false) {
  const root = await mkdtemp(join(tmpdir(), `pixel-isolated-codex-${mode}-`)); t.after(() => rm(root, { recursive: true, force: true })); if (process.platform !== "win32") await chmod(root, 0o700);
  const policy = structuredClone(policyTemplate); policy.enabled = true; policy.taskClasses["structural-review"].enabled = true;
  policy.transport.runnerImageRef = `fixture/pixel-codex@sha256:${ids.runnerManifest}`; policy.transport.runnerImageId = `sha256:${ids.runnerImage}`; policy.transport.proxyImageDigest = `sha256:${ids.proxyImage}`; policy.transport.codexBinarySha256 = "f".repeat(64); policy.transport.entrypointSha256 = "1".repeat(64);
  if (api) { policy.provider.authMode = "api-key"; policy.transport.allowedHosts = ["api.openai.com"]; policy.credentialCustody = { mode: "broker-private-api-key-file", credentialId: "codex-api-key", maxBytes: 8192, mutableRefresh: false, runtimeAccess: "stdin-only-ephemeral-login", materialProjectedToPixel: false, ambientEnvironment: false }; policy.provider.billing = { mode: "metered", boundary: "Separately billed OpenAI API Platform usage; ChatGPT subscription access does not cover this route.", currency: "USD", inputMicrosPerMillionTokens: 1, outputMicrosPerMillionTokens: 2, source: "https://openai.com/api/pricing/", asOf: "2026-08-10", maxEstimatedCostMicros: 1000000 }; }
  const egressPolicy = { $schema: "https://osmantic.com/pixel/schemas/work-codex-egress-policy-v1.schema.json", schemaVersion: 1, mode: "exact-connect-host-allowlist", allowedHosts: policy.transport.allowedHosts, allowedPorts: [443], denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, denyConnectToUnlistedHosts: true, maxConcurrentTunnels: 4, maxTunnelBytes: 33554432, connectTimeoutSeconds: 10, idleTimeoutSeconds: 60, logRequestBodies: false, logResponseBodies: false, boundary: egressBoundary };
  const proxyPolicyPath = join(root, "egress-policy.json"); await writeFile(proxyPolicyPath, canonical(egressPolicy), { mode: 0o600 }); if (process.platform !== "win32") await chmod(proxyPolicyPath, 0o600); policy.transport.proxyPolicySha256 = sha(egressPolicy);
  const credentialRoot = join(root, "credential"); await import("node:fs/promises").then(({ mkdir }) => mkdir(credentialRoot, { mode: 0o700 })); if (process.platform !== "win32") await chmod(credentialRoot, 0o700);
  const credential = api ? "sk-fixture_abcdefghijklmnopqrstuvwxyz123456" : '{"auth_mode":"chatgpt","fixture":"private-material"}', credentialPath = join(credentialRoot, api ? "provider-key" : "auth.json"); await writeFile(credentialPath, credential, { mode: 0o600 }); if (process.platform !== "win32") await chmod(credentialPath, 0o600);
  const custody = await inspectWorkCodexCredentialCustody({ policy, credentialPath, now: new Date(baseTime), suffix: "000000000001" });
  const content = "Customer Acme North has a structural boundary.";
  const request = { $schema: "https://osmantic.com/pixel/schemas/work-codex-request-v1.schema.json", schemaVersion: 1, requestId: `workcodexrequest-${baseTime}-000000000010`, createdAt: new Date(baseTime).toISOString(), expiresAt: new Date(baseTime + 20 * 60000).toISOString(), jobId: `work-${baseTime}-000000000011`, checkpointSha256: sha("checkpoint"), ownerId: "owner-private", clientId: "client-private", taskClass: "structural-review", egressMode: "structural-only", classification: "internal", dataCategories: ["structural"], localAttempt: { attempts: 2, outcome: "completed-needs-review", reasonCodes: ["quality-check"], receiptSha256: sha("attempt") }, objective: "Review Acme North.", constraints: ["Do not expose names."], acceptanceCriteria: ["Return advice."], sensitiveTerms: [{ kind: "customer", value: "Acme North" }], documents: [{ documentId: "private-document", kind: "structure", language: "text", content, contentSha256: sha(content) }], maxOutputTokens: 1024, boundary: "Private local request for one sanitized Codex work proposal. Compilation does not authorize or invoke a provider, expose credentials, or grant any external effect." };
  const { plan } = compileWorkCodexPreview({ request, policy, now: new Date(baseTime + 1000), suffix: "000000000012", mappingNonce: sha("mapping") }), payload = { capsule: plan.capsule, outputSchema, provider: plan.provider, limits: plan.limits, planSha256: sha(plan) };
  const runtime = { networkName: "pixel-codex-net", networkId: ids.network, proxyName: "pixel-codex-proxy", proxyContainerId: ids.proxy, proxyIp: "172.30.8.3", externalNetworkName: "pixel-egress-net", proxyPolicyPath, brokerUid: 1000, brokerGid: 1000 };
  const script = join(root, "fake-docker.mjs"), captureRoot = join(root, "capture"); await import("node:fs/promises").then(({ mkdir }) => mkdir(captureRoot, { mode: 0o700 }));
  await writeFile(script, fakeDockerSource({ mode, output: canonical(providerOutput()), credential, policy, runtime, captureRoot }), { mode: 0o700 }); if (process.platform !== "win32") await chmod(script, 0o700);
  const runtimeRoot = join(root, "runtime"), adapter = createIsolatedWorkCodexCliAdapter({ policy, credentialHandle: custody.handle, dockerPath: process.execPath, dockerBootstrapArguments: [script], runtimeRoot, runtime, runtimeId: "000000000099", timeoutMilliseconds: ["timeout", "output-flood"].includes(mode) ? 250 : 5000, allowNonLinuxTests: true });
  return { root, policy, credential, custody, credentialPath, payload, runtimeRoot, captureRoot, adapter };
}

test("isolated adapter admits one exact inspected proxy path and keeps credentials off argv, environment, and captures", async (t) => {
  const value = await fixture(t), result = await value.adapter.run(value.payload); assert.equal(result.externalState, "invoked-once"); assert.deepEqual(result.usage, { inputTokens: 300, outputTokens: 70 });
  const capture = JSON.parse(await readFile(join(value.captureRoot, "run.json"), "utf8")); assert.equal(capture.credentialMatched, true); assert.equal(capture.payload.planSha256, value.payload.planSha256);
  const serialized = JSON.stringify(capture); for (const forbidden of [value.credential, "Acme North", "owner-private", "client-private", "private-document"]) assert.equal(serialized.includes(forbidden), false, forbidden);
  for (const required of ["--pull", "never", "--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "--network", "pixel-codex-net", "--dns", "127.0.0.1", "--privileged=false", "--log-driver", "none", "PIXEL_CODEX_PROXY_URL=http://172.30.8.3:3128"]) assert.ok(capture.args.includes(required), required);
  assert.ok(capture.args.includes("/run/pixel:rw,nosuid,nodev,noexec,size=80m,mode=0700,uid=1000,gid=1000")); assert.equal(capture.args.some((entry) => entry.startsWith("/run/pixel/home:")), false);
  for (const forbidden of ["OPENAI_API_KEY", "CODEX_API_KEY", "DOCKER_HOST"]) assert.equal(capture.environmentKeys.includes(forbidden), false);
  assert.match(await readWorkCodexChatgptAuthCache({ policy: value.policy, handle: value.custody.handle }), /rotated-by-container/u);
  await assert.rejects(readFile(value.runtimeRoot), /ENOENT|EISDIR/u); await assert.rejects(() => value.adapter.run(value.payload), /single-use/u);
});

test("API credential uses the same stdin-only container boundary without a persistent refresh", async (t) => {
  const value = await fixture(t, "success", true), result = await value.adapter.run(value.payload); assert.equal(result.externalState, "invoked-once");
  const capture = JSON.parse(await readFile(join(value.captureRoot, "run.json"), "utf8")); assert.equal(capture.credentialMatched, true); assert.equal(JSON.stringify(capture).includes(value.credential), false); assert.equal(await readFile(value.credentialPath, "utf8"), value.credential);
});

test("substituted images, wide networks, and privileged proxies fail before credential projection", async (t) => {
  for (const mode of ["wide-network", "privileged-proxy"]) await t.test(mode, async (subtest) => {
    const value = await fixture(subtest, mode); await assert.rejects(() => value.adapter.run(value.payload), /network|proxy/u); await assert.rejects(readFile(join(value.captureRoot, "run.json")), /ENOENT/u);
  });
  const value = await fixture(t, "success"); value.policy.transport.runnerImageId = `sha256:${"9".repeat(64)}`;
  await assert.rejects(() => value.adapter.run(value.payload), /payload differs|image differs/u);
});

test("timeouts, output floods, tool events, and nonzero exits fail closed, clean up, and never retry", async (t) => {
  for (const mode of ["timeout", "output-flood", "tool", "nonzero"]) await t.test(mode, async (subtest) => {
    const value = await fixture(subtest, mode); const started = Date.now(); await assert.rejects(() => value.adapter.run(value.payload), IsolatedWorkCodexCliError); assert.ok(Date.now() - started < 5000); await assert.rejects(readFile(value.runtimeRoot), /ENOENT|EISDIR/u); assert.ok(JSON.parse(await readFile(join(value.captureRoot, "cleanup.json"), "utf8")).args.includes("--force")); await assert.rejects(() => value.adapter.run(value.payload), /single-use/u);
  });
});

test("unverified forced cleanup fails the turn and does not install a returned auth refresh", async (t) => {
  const value = await fixture(t, "cleanup-failure");
  await assert.rejects(() => value.adapter.run(value.payload), /cleanup left a named container/u);
  assert.match(await readWorkCodexChatgptAuthCache({ policy: value.policy, handle: value.custody.handle }), /private-material/u);
  await assert.rejects(readFile(value.runtimeRoot), /ENOENT|EISDIR/u);
});
