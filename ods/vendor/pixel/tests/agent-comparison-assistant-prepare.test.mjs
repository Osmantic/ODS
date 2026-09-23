import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  ASSISTANT_PREPARATION_BOUNDARY, AssistantPreparationError, prepareAssistantRuntime,
} from "../deploy/agent-comparison/assistant-runtime-prepare.mjs";
import { canonical } from "../deploy/work-broker/broker.mjs";

const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const TEMPLATE_BOUNDARY = "Owner-private credential-free preparation inputs for one disposable Pixel portal Assistant comparison runtime. The template grants no model start, task execution, provider credential, external effect, policy change, acceptance, publication, deployment, or promotion authority.";
const sha = (value) => createHash("sha256").update(Buffer.isBuffer(value) || typeof value === "string" ? value : canonical(value)).digest("hex");

function octal(value, length) { return `${value.toString(8).padStart(length - 1, "0")}\0`; }
function header(name, size) {
  const value = Buffer.alloc(512);
  value.write(name, 0, 100, "utf8"); value.write(octal(0o644, 8), 100, 8, "ascii");
  value.write(octal(0, 8), 108, 8, "ascii"); value.write(octal(0, 8), 116, 8, "ascii");
  value.write(octal(size, 12), 124, 12, "ascii"); value.write(octal(0, 12), 136, 12, "ascii");
  value.fill(0x20, 148, 156); value[156] = 0x30;
  value.write("ustar\0", 257, 6, "latin1"); value.write("00", 263, 2, "ascii");
  let checksum = 0; for (const byte of value) checksum += byte;
  value.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii");
  return value;
}
function archive(name, content) {
  const payload = Buffer.from(content, "utf8"), padding = (512 - payload.length % 512) % 512;
  return Buffer.concat([header(name, payload.length), payload, Buffer.alloc(padding), Buffer.alloc(1024)]);
}

async function fixture(name) {
  const base = await mkdtemp(join(tmpdir(), `pixel-assistant-prepare-${name}-`));
  const runRoot = join(base, "run"), cache = join(base, "cache"), journal = join(base, "journal");
  await Promise.all([mkdir(runRoot, { mode: 0o700 }), mkdir(cache, { mode: 0o700 }), mkdir(journal, { mode: 0o700 })]);
  const openclaw = join(base, "openclaw"); await writeFile(openclaw, "fixture\n", { mode: 0o700 }); await chmod(openclaw, 0o700);
  const sourceBytes = archive("source/main.txt", "original fixture\n"), sourceArchivePath = join(base, "source.tar");
  await writeFile(sourceArchivePath, sourceBytes, { mode: 0o600 });
  const assistantTemplatePath = join(base, "assistant-template.json");
  await writeFile(assistantTemplatePath, `${JSON.stringify({
    $schema: "https://osmantic.com/pixel/schemas/portal-outcome-assistant-template-v1.schema.json",
    schemaVersion: 1, openclawBinaryPath: openclaw, sandboxImage: "pixel-sandbox:test",
    embeddingModel: "embedding-fixture.gguf", embeddingCachePath: cache,
    searxngBaseUrl: "http://127.0.0.1:18890", proxyListenPort: 18881,
    chatEnvironment: {
      PIXEL_LIMB_EMAIL_ENABLED: "1", PIXEL_LIMB_CALENDAR_ENABLED: "1",
      PIXEL_LIMB_OPERATIONS_ENABLED: "1", PIXEL_LIMB_FRONTIER_ENABLED: "1",
      PIXEL_CALENDAR_DIRECT_ENABLED: "1", PIXEL_SOURCE_STALE_AFTER_MS: "60000",
      SEARXNG_BASE_URL: "http://should-not-win.invalid",
    },
    actionJournalRoots: [journal], boundary: TEMPLATE_BOUNDARY,
  }, null, 2)}\n`, { mode: 0o600 });
  const sourceReference = { mediaType: "application/x-tar", sha256: sha(sourceBytes), bytes: sourceBytes.length };
  const request = Buffer.from("Inspect the admitted workspace with the exact local lease.\n", "utf8");
  const modelContract = {
    modelId: "DeepSeek-V4-Flash-0731", artifact: { sha256: "a".repeat(64) },
    runtime: { implementation: "vllm", imageDigest: `sha256:${"b".repeat(64)}`, contextWindow: 131072 },
  };
  const inferenceContract = {
    sampling: {
      temperaturePermille: 700, topPPermille: 950, topK: 40, minPPermille: 50,
      repeatPenaltyPermille: 1100, seed: 42, reasoningEffort: "backend-default", reasoningVisibility: "hidden",
    },
    request: {
      wireApi: "openai-chat-completions", stream: true, maxOutputTokens: 4096,
      toolEncoding: "function", requestFieldPolicySha256: "c".repeat(64), promptCachePolicy: "empty-at-run-start",
    },
  };
  const execution = {
    request: {
      requestSha256: sha(request), source: sourceReference,
      modelContractSha256: sha(modelContract), inferenceContractSha256: sha(inferenceContract),
    },
    plan: {
      profile: "assistant", requestSha256: sha(request),
      allowedTools: ["read", "web_search"],
      limbs: { email: false, calendar: false, operations: false, web: true },
      limits: {
        maxRuntimeSeconds: 600, maxModelRequests: 20, maxInputTokens: 100000,
        maxOutputTokens: 20000, maxDiskBytes: 16777216, maxNetworkBytes: 10485760,
      },
    },
  };
  const qualification = {
    qualificationId: "modelqual-1786690000000-abcdef123456", receiptSha256: "d".repeat(64),
    casesSha256: "e".repeat(64), evaluatorSha256: "f".repeat(64), profile: "assistant",
    maxContextTokens: 131072, maxOutputTokens: 4096, exactUsage: true,
  };
  return {
    root: ROOT, runRoot, runId: "outcomerun-1786690000000-abcdef123456",
    sourceArchivePath, sourceReference, assistantTemplatePath, execution, modelContract, inferenceContract,
    qualification, backendOrigin: "http://127.0.0.1:18080/", nodeBinary: process.execPath,
    rendererPath: join(ROOT, "scripts", "render-config.mjs"),
    proxyLauncherPath: join(ROOT, "deploy", "agent-comparison", "assistant-model-proxy.mjs"),
    archiveLimits: { maxEntries: 32, maxFileBytes: 1048576 },
  };
}

test("Assistant preparation materializes exact source and derives runtime capability flags from the admitted lease", async () => {
  const value = await fixture("green"), receipt = await prepareAssistantRuntime(value);
  assert.equal(receipt.boundary, ASSISTANT_PREPARATION_BOUNDARY);
  assert.equal(receipt.assistantRootPath, join(value.runRoot, "assistant"));
  assert.equal(receipt.openclawBinarySha256, sha(await readFile(receipt.openclawBinaryPath)));
  assert.equal(receipt.openclawBinaryBytes, (await readFile(receipt.openclawBinaryPath)).length);
  assert.equal(receipt.nodeBinarySha256, sha(await readFile(receipt.nodeBinaryPath)));
  assert.equal(receipt.nodeBinaryBytes, (await readFile(receipt.nodeBinaryPath)).length);
  assert.equal(await readFile(join(receipt.workspacePath, "source", "main.txt"), "utf8"), "original fixture\n");
  assert.deepEqual(receipt.allowedTools, value.execution.plan.allowedTools);
  assert.deepEqual(receipt.chatEnvironment, {
    PIXEL_SOURCE_STALE_AFTER_MS: "60000", PIXEL_CALENDAR_DIRECT_ENABLED: "0",
    PIXEL_LIMB_EMAIL_ENABLED: "0", PIXEL_LIMB_CALENDAR_ENABLED: "0", PIXEL_LIMB_SOCIAL_ENABLED: "0",
    PIXEL_LIMB_OPERATIONS_ENABLED: "0", PIXEL_LIMB_FRONTIER_ENABLED: "0",
    SEARXNG_BASE_URL: "http://127.0.0.1:18890",
  });
  const inventory = JSON.parse(await readFile(receipt.sourceInventoryPath, "utf8"));
  assert.equal(inventory.treeSha256, receipt.workspaceTreeSha256);
  assert.deepEqual(inventory.entries.map((entry) => entry.path), ["source/main.txt"]);
  const rendered = JSON.parse(await readFile(join(receipt.openclawHomePath, "openclaw.json"), "utf8"));
  assert.deepEqual(rendered.tools.sandbox.tools.allow, value.execution.plan.allowedTools);
  assert.equal(rendered.agents.list.find((agent) => agent.id === "pixel").workspace, receipt.workspacePath);
  const proxy = JSON.parse(await readFile(receipt.proxyConfigPath, "utf8"));
  assert.equal(proxy.qualification.profile, "assistant");
  assert.deepEqual(proxy.allowedTools, value.execution.plan.allowedTools);
});

test("Assistant preparation accepts an explicitly selected trusted system OpenClaw executable", {
  skip: !process.env.PIXEL_TEST_SYSTEM_OPENCLAW,
}, async () => {
  const value = await fixture("system-openclaw");
  const template = JSON.parse(await readFile(value.assistantTemplatePath, "utf8"));
  template.openclawBinaryPath = process.env.PIXEL_TEST_SYSTEM_OPENCLAW;
  await writeFile(value.assistantTemplatePath, `${JSON.stringify(template, null, 2)}\n`);
  const receipt = await prepareAssistantRuntime(value);
  assert.equal(receipt.openclawBinaryPath, process.env.PIXEL_TEST_SYSTEM_OPENCLAW);
  assert.equal(receipt.openclawBinarySha256, sha(await readFile(receipt.openclawBinaryPath)));
  assert.equal(receipt.openclawBinaryBytes, (await readFile(receipt.openclawBinaryPath)).length);
});

test("Assistant preparation fails closed on qualification, source, and rendered lease drift", async () => {
  const wrongQualification = await fixture("qualification"); wrongQualification.qualification.profile = "builder";
  await assert.rejects(prepareAssistantRuntime(wrongQualification), AssistantPreparationError);

  const wrongSource = await fixture("source");
  wrongSource.sourceReference.sha256 = "0".repeat(64); wrongSource.execution.request.source.sha256 = "0".repeat(64);
  await assert.rejects(prepareAssistantRuntime(wrongSource), AssistantPreparationError);

  const widened = await fixture("widened");
  await assert.rejects(prepareAssistantRuntime(widened, {
    renderConfig: async ({ outputPath, environment }) => {
      const allowed = JSON.parse(environment.PIXEL_AGENT_TOOL_ALLOWLIST);
      await writeFile(outputPath, `${JSON.stringify({
        tools: { sandbox: { tools: { allow: [...allowed, "exec"] } } },
        agents: { list: [{ id: "pixel", workspace: environment.PIXEL_WORKSPACE, model: `local/${environment.PIXEL_MODEL_ID}` }] },
      })}\n`, { mode: 0o600 });
    },
  }), /tool lease differs/u);

  if (process.platform !== "win32") {
    const unsafeNode = await fixture("unsafe-node"), unsafeNodePath = join(unsafeNode.runRoot, "unsafe-node");
    await writeFile(unsafeNodePath, "not a trusted runtime\n", { mode: 0o777 });
    await chmod(unsafeNodePath, 0o777); unsafeNode.nodeBinary = unsafeNodePath;
    await assert.rejects(prepareAssistantRuntime(unsafeNode), /unsafe ownership or mode/u);

    const unsafeOpenClaw = await fixture("unsafe-openclaw");
    const template = JSON.parse(await readFile(unsafeOpenClaw.assistantTemplatePath, "utf8"));
    await chmod(template.openclawBinaryPath, 0o777);
    await assert.rejects(prepareAssistantRuntime(unsafeOpenClaw), /unsafe ownership or mode/u);
  }
});
