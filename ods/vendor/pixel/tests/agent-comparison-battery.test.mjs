import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { validateJsonSchema } from "../scripts/lib/json-schema.mjs";

const run = promisify(execFile);
const corpusUrl = new URL("../security-evals/agent-comparison/task-battery-v1.json", import.meta.url);
const schemaUrl = new URL("../schemas/agent-comparison-task-battery-v1.schema.json", import.meta.url);
const trialUrl = new URL("../security-evals/portal-user-trial/trial-journeys-v1.json", import.meta.url);
const productUrl = new URL("../security-evals/portal-user-journeys/corpus-v1.json", import.meta.url);

async function pythonAvailable() {
  try { await run("python3", ["--version"]); return true; } catch { return false; }
}

test("agent-comparison battery is structurally sound and non-gameable in shape", async () => {
  const corpus = JSON.parse(await readFile(corpusUrl, "utf8"));
  const schema = JSON.parse(await readFile(schemaUrl, "utf8"));
  assert.equal(corpus.$schema, schema.$id);
  assert.deepEqual(validateJsonSchema(corpus, schema), []);
  assert.ok(corpus.tasks.length >= 20, "battery has the full task set");
  const ids = new Set();
  const axes = new Set();
  const rehearsalJourneys = new Set();
  const trialJourneys = new Set();
  const exactHeldoutAxes = new Set();
  const formalProductTasks = [];
  let exactHeldoutTasks = 0;
  let repositoryScaleHeldoutTasks = 0;
  for (const t of corpus.tasks) {
    assert.ok(!ids.has(t.taskId), `duplicate task ${t.taskId}`);
    ids.add(t.taskId);
    axes.add(t.axis);
    assert.ok(Object.keys(t.workspace).length >= 1, `${t.taskId} has an empty workspace`);
    if (t.profile === "researcher") {
      assert.equal(t.verify, null, `${t.taskId} must use the product citation verifier rather than a dead workspace verifier`);
      assert.ok(Boolean(t.rehearsal) !== Boolean(t.researchFixture), `${t.taskId} must bind exactly one admitted offline research corpus`);
      if (t.rehearsal) assert.ok(Object.keys(t.rehearsal.fixture.actions).some((action) => action === "research.search"));
      else {
        assert.equal(t.researchFixture.operation, "pixel-portal-outcome-research-fixture");
        assert.ok(t.researchFixture.sources.length >= 1);
        assert.ok(Object.values(t.researchFixture.authority).every((value) => value === false));
      }
    } else {
      // Builder non-gameability: independently execute the artifact and use a fixed marker.
      assert.equal(t.verify.command[0], "python3", `${t.taskId} verifier must run python3`);
      assert.match(t.verify.command.at(-1), /import |open\(|subprocess|csv|json/, `${t.taskId} verifier does not independently execute the artifact`);
      assert.match(t.verify.expectStdout.trim(), /^[A-Z0-9_]+$/, `${t.taskId} success token must be a fixed marker`);
    }
    if (t.rehearsal) {
      assert.equal(t.rehearsal.proofClass, "deterministic-non-promotional-rehearsal");
      assert.equal(t.source, t.rehearsal.partition === "held-out" ? "realistic-held-out-rehearsal" : "sanitized-user-trial-rehearsal");
      assert.equal(t.rehearsal.fixture.operation, "pixel-agent-comparison-fixture");
      assert.match(t.rehearsal.fixture.boundary, /no live provider.*product proof.*promotion authority/u);
      rehearsalJourneys.add(t.rehearsal.journeyId);
      for (const id of t.rehearsal.trialJourneyIds) trialJourneys.add(id);
    }
    if (t.trialProof) {
      formalProductTasks.push(t);
      assert.equal(t.trialProof.proofClass, "formal-product-path-task");
      assert.ok(["assistant", "builder", "controller", "researcher"].includes(t.profile));
      assert.ok(["tuning", "held-out"].includes(t.partition));
      assert.equal(t.rehearsal, undefined);
      assert.ok(t.trialProof.trialJourneyIds.length >= 1);
      if (t.profile === "researcher") assert.ok(t.researchFixture, `${t.taskId} must bind a formal research corpus`);
      else {
        assert.ok(t.immutableWorkspace?.length >= 1);
        for (const name of t.immutableWorkspace) assert.ok(Object.hasOwn(t.workspace, name), `${t.taskId} protects a missing path`);
      }
    }
    if (!t.rehearsal && t.partition === "held-out") {
      exactHeldoutTasks += 1;
      exactHeldoutAxes.add(t.axis);
      if (t.scale?.class === "repository") {
        repositoryScaleHeldoutTasks += 1;
        const workspaceBytes = Object.values(t.workspace).reduce((total, value) => total + Buffer.byteLength(value), 0);
        assert.ok(Object.keys(t.workspace).length >= t.scale.minimumFiles, `${t.taskId} overstates its repository file count`);
        assert.ok(workspaceBytes >= t.scale.minimumBytes, `${t.taskId} overstates its repository byte count`);
        assert.ok(new Set(Object.keys(t.workspace).map((name) => dirname(name))).size >= 3,
          `${t.taskId} must exercise at least three repository directories`);
      }
    }
  }
  assert.ok(exactHeldoutTasks >= 10, "battery must reserve at least ten exact workspace tasks for held-out execution");
  assert.ok(exactHeldoutAxes.size >= 8, "exact held-out work must span at least eight realistic capability axes");
  assert.ok(repositoryScaleHeldoutTasks >= 5, "held-out execution must include at least five mechanically repository-scale tasks");
  assert.ok(formalProductTasks.length >= 6, "battery must define broad formal product-path work for the owner trial");
  assert.deepEqual(new Set(formalProductTasks.map(({ profile }) => profile)), new Set(["assistant", "builder", "controller", "researcher"]));
  // Coverage across the stress axes the goal names.
  for (const required of ["security-injection", "idempotency", "crash-recovery", "data-integrity", "refactor", "bug-fix"]) {
    assert.ok(axes.has(required), `battery is missing the ${required} axis`);
  }
  for (const required of ["fresh-owner-briefing", "cited-current-research", "calendar-unknown-outcome", "scoped-fleet-inventory", "sanitized-remote-spillover", "chat-inline-authority", "deep-work-crash-recovery"]) {
    assert.ok(rehearsalJourneys.has(required), `battery is missing executable rehearsal coverage for ${required}`);
  }
  const trial = JSON.parse(await readFile(trialUrl, "utf8"));
  assert.deepEqual(trialJourneys, new Set(trial.journeys.map(({ id }) => id)), "every sanitized 535-line trial journey must bind executable rehearsal coverage");
  const product = JSON.parse(await readFile(productUrl, "utf8"));
  const trialProfiles = new Map(trial.journeys.map(({ id, profile }) => [id, profile]));
  const productProfiles = new Map(product.journeys.map(({ id, profile }) => [id, profile]));
  for (const task of formalProductTasks) {
    assert.equal(productProfiles.get(task.trialProof.productJourneyId), task.profile, `${task.taskId} targets a different product profile`);
    for (const id of task.trialProof.trialJourneyIds) assert.equal(trialProfiles.get(id), task.profile, `${task.taskId} targets a different trial profile`);
  }
});

test("battery reserves both tuning and held-out partitions for every execution profile", async () => {
  const corpus = JSON.parse(await readFile(corpusUrl, "utf8"));
  const byProfile = new Map();
  for (const t of corpus.tasks) {
    const profile = t.profile ?? "builder";
    const partition = t.partition ?? t.rehearsal?.partition ?? "tuning";
    if (!byProfile.has(profile)) byProfile.set(profile, new Set());
    byProfile.get(profile).add(partition);
  }
  for (const profile of ["assistant", "builder", "controller", "researcher"]) {
    const partitions = byProfile.get(profile);
    assert.ok(partitions, `${profile} has tasks in the battery`);
    assert.deepEqual([...partitions].sort(), ["held-out", "tuning"],
      `${profile} must have both tuning and held-out partitions`);
  }
});

test("every battery task genuinely discriminates: its verifier FAILS on the untouched workspace", async (t) => {
  if (!(await pythonAvailable())) { t.skip("python3 unavailable in this environment"); return; }
  const corpus = JSON.parse(await readFile(corpusUrl, "utf8"));
  const base = await mkdtemp(join(tmpdir(), "pixel-battery-discriminate-"));
  try {
    for (const task of corpus.tasks) {
      if (task.profile === "researcher") {
        assert.equal(task.verify, null, `${task.taskId} must be verified by retained research evidence`);
        continue;
      }
      const dir = join(base, task.taskId);
      await mkdir(dir, { recursive: true });
      for (const [rel, content] of Object.entries(task.workspace)) {
        const fp = join(dir, rel);
        await mkdir(dirname(fp), { recursive: true });
        await writeFile(fp, content);
      }
      let stdout = "";
      try {
        const res = await run(task.verify.command[0], task.verify.command.slice(1), { cwd: dir, timeout: 30000 });
        stdout = res.stdout;
      } catch (err) {
        stdout = (err && err.stdout) || "";
      }
      // On the UNTOUCHED workspace (before any agent acts) the verifier must NOT already pass —
      // otherwise the task tests nothing. This mechanically proves every committed task requires real work.
      assert.notEqual(stdout.trim(), task.verify.expectStdout.trim(),
        `${task.taskId} passes on the untouched workspace — it does not discriminate`);
    }
  } finally {
    await rm(base, { recursive: true, force: true });
  }
});
