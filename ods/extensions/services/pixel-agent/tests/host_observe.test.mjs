import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import {
  createHostCommandProposeTool,
  createHostObserveTool,
  testing,
} from "../plugin/host-observe.mjs";

// Match the external broker's atomic_json contract: a visible result is final,
// never an empty file between open() and write(). Invalid final bytes still fail.
async function publishResult(filename, value) {
  const temporary = `${filename}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value)}\n`, {
    encoding: "utf8",
    mode: 0o640,
  });
  await rename(temporary, filename);
}

test("host observation accepts only unique fixed read-only actions", () => {
  assert.deepEqual(
    testing.normalizedActions(["host.identity", "host.kernel"]),
    ["host.identity", "host.kernel"]
  );
  assert.throws(() => testing.normalizedActions([]), /invalid host observation actions/);
  assert.throws(
    () => testing.normalizedActions(["host.identity", "host.identity"]),
    /duplicate host observation action/
  );
  assert.throws(
    () => testing.normalizedActions(["raw-shell"]),
    /invalid host observation action/
  );
});

test("host observation schema exposes no broker target, command, or approval input", async () => {
  const tool = createHostObserveTool();
  assert.equal(tool.name, "pixel_ods_host_observe");
  assert.deepEqual(tool.parameters.required, ["actions"]);
  assert.deepEqual(Object.keys(tool.parameters.properties), [
    "actions", "includeOdsStatus", "peer", "ports",
  ]);
  assert.equal(tool.parameters.additionalProperties, false);
  assert.equal(tool.parameters.properties.actions.uniqueItems, true);
  assert.equal(tool.parameters.properties.actions.items.enum.includes("host.identity"), true);
  assert.equal(tool.parameters.properties.actions.items.enum.includes("host.gpu"), true);
  assert.equal(tool.parameters.properties.actions.items.enum.includes("host.tailscale"), true);
  assert.equal(tool.parameters.properties.actions.items.enum.includes("host.network-peer"), true);
  assert.equal(tool.parameters.properties.actions.items.enum.includes("raw-shell"), false);
  assert.deepEqual(tool.parameters.properties.includeOdsStatus, { type: "boolean" });

  const result = await tool.execute("call-1", { actions: ["raw-shell"] });
  assert.equal(result.isError, true);
  assert.deepEqual(Object.keys(result.details).sort(), ["boundaryNotice", "status"]);
  assert.doesNotMatch(result.content[0].text, /raw-shell|var\/lib|operations job ID/);
});

test("host observation binds one private peer and bounded ports into the workflow", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-host-peer-"));
  const requestDir = join(root, "requests");
  const resultDir = join(root, "results");
  await mkdir(requestDir);
  await mkdir(resultDir);
  try {
    const tool = createHostObserveTool({
      requestDir,
      resultDir,
      timeoutMs: 2_000,
      pollIntervalMs: 5,
    });
    const pending = tool.execute("call-peer", {
      actions: ["host.tailscale", "host.network-peer"],
      peer: "Strixy",
      ports: [22, 3389],
    });
    let names = [];
    for (let attempt = 0; attempt < 100 && names.length === 0; attempt += 1) {
      names = (await readdir(requestDir)).filter((name) => name.endsWith(".json"));
      if (names.length === 0) await delay(5);
    }
    const request = JSON.parse(await readFile(join(requestDir, names[0]), "utf8"));
    assert.deepEqual(request.steps, [
      { id: "observe-1", target: "ods-host", action: "host.tailscale" },
      {
        id: "observe-2",
        target: "ods-host",
        action: "host.network-peer",
        parameters: { peer: "Strixy", ports: "22,3389" },
      },
    ]);
    await publishResult(
      join(resultDir, `${request.jobId}.json`),
      {
        schemaVersion: 2,
        jobId: request.jobId,
        status: "succeeded",
        steps: [],
      }
    );
    const result = await pending;
    assert.equal(result.details.jobId, request.jobId);
    assert.equal(result.details.status, "succeeded");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("host peer observation rejects ranges, URLs, public paths, and oversized port sets", () => {
  for (const value of [
    "192.168.0.0/24", "https://strixy", "Strixy;whoami", "../Strixy",
    "8.8.8.8", "2606:4700:4700::1111", "localhost",
  ]) {
    assert.throws(() => testing.normalizedPeer(value), /invalid network peer/);
  }
  assert.deepEqual(testing.normalizedPorts(undefined), [22, 80, 443, 3389, 5985, 5986]);
  assert.throws(
    () => testing.normalizedPorts([1, 2, 3, 4, 5, 6, 7, 8, 9]),
    /invalid network peer ports/
  );
});

test("host command adapter publishes exact protocol bytes and returns one approval receipt", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-host-command-"));
  const requestDir = join(root, "requests");
  const resultDir = join(root, "results");
  await mkdir(requestDir);
  await mkdir(resultDir);
  const command = "printf 'HOST_COMMAND_OK\\n'; /usr/bin/uname -sr; /usr/bin/id -un";
  try {
    const tool = createHostCommandProposeTool({
      requestDir,
      resultDir,
      timeoutMs: 2_000,
      pollIntervalMs: 5,
    });
    assert.equal(tool.name, "pixel_ods_host_command_propose");
    assert.deepEqual(tool.parameters.required, ["command"]);
    assert.deepEqual(Object.keys(tool.parameters.properties), ["command"]);
    assert.equal(tool.parameters.additionalProperties, false);

    const pending = tool.execute("call-1", { command });
    let names = [];
    for (let attempt = 0; attempt < 100 && names.length === 0; attempt += 1) {
      names = (await readdir(requestDir)).filter((name) => name.endsWith(".json"));
      if (names.length === 0) await delay(5);
    }
    assert.equal(names.length, 1);
    const request = JSON.parse(await readFile(join(requestDir, names[0]), "utf8"));
    assert.deepEqual(Object.keys(request).sort(), [
      "boundary",
      "command",
      "createdAt",
      "jobId",
      "kind",
      "reason",
      "requester",
      "schemaVersion",
      "target",
    ]);
    assert.equal(request.schemaVersion, 1);
    assert.match(request.jobId, /^ops-[0-9]{13}-[a-f0-9]{12}$/);
    assert.equal(request.kind, "shell");
    assert.equal(request.target, "ods-host");
    assert.equal(request.command, command);
    assert.equal(
      request.reason,
      "Owner requested one protected command from the local ODS host, possibly to an explicitly named SSH destination."
    );
    assert.doesNotMatch(request.boundary, /approve/i);

    const planHash = "a".repeat(64);
    await publishResult(
      join(resultDir, `${request.jobId}.json`),
      {
        schemaVersion: 2,
        jobId: request.jobId,
        status: "awaiting-approval",
        approvalRequired: true,
        planHash,
      }
    );
    const result = await pending;
    assert.equal(result.isError, undefined);
    assert.equal(result.details.jobId, request.jobId);
    assert.equal(result.details.status, "awaiting-approval");
    assert.equal(result.details.approvalRequired, true);
    assert.equal(result.details.planHash, planHash);
    assert.equal(result.details.waitTimedOut, false);
    assert.match(result.details.boundaryNotice, /cannot approve/i);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("host command adapter rejects unbounded commands without publishing a request", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-host-command-invalid-"));
  const requestDir = join(root, "requests");
  const resultDir = join(root, "results");
  await mkdir(requestDir);
  await mkdir(resultDir);
  try {
    const tool = createHostCommandProposeTool({ requestDir, resultDir, timeoutMs: 10 });
    for (const command of ["", "\0", "é".repeat(9_000)]) {
      const result = await tool.execute("call-invalid", { command });
      assert.equal(result.isError, true);
      assert.equal(result.details.status, "unavailable");
    }
    assert.deepEqual(await readdir(requestDir), []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("host command adapter rejects a result whose embedded job ID does not match", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-host-command-mismatch-"));
  const requestDir = join(root, "requests");
  const resultDir = join(root, "results");
  await mkdir(requestDir);
  await mkdir(resultDir);
  try {
    const tool = createHostCommandProposeTool({
      requestDir,
      resultDir,
      timeoutMs: 2_000,
      pollIntervalMs: 5,
    });
    const pending = tool.execute("call-mismatch", { command: "uname -sr" });
    let names = [];
    for (let attempt = 0; attempt < 100 && names.length === 0; attempt += 1) {
      names = (await readdir(requestDir)).filter((name) => name.endsWith(".json"));
      if (names.length === 0) await delay(5);
    }
    assert.equal(names.length, 1);
    const request = JSON.parse(await readFile(join(requestDir, names[0]), "utf8"));
    await publishResult(
      join(resultDir, `${request.jobId}.json`),
      {
        schemaVersion: 2,
        jobId: "ops-1234567890123-abcdef123456",
        status: "awaiting-approval",
        approvalRequired: true,
        planHash: "b".repeat(64),
      }
    );
    const result = await pending;
    assert.equal(result.isError, true);
    assert.equal(result.details.status, "unavailable");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
