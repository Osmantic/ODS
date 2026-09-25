import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const corpusUrl = new URL("../security-evals/portal-user-trial/trial-journeys-v1.json", import.meta.url);
const schemaUrl = new URL("../schemas/portal-user-trial-journey-v1.schema.json", import.meta.url);

// The private transcript must never be reproduced in the committed corpus. This denylist holds
// tokens that appear in the real trial content (people, organizations, places, private detail).
// If any surfaces in the sanitized corpus, sanitization has failed and the test blocks it.
const PERSONAL_DENYLIST = [
  "Grace", "Ahmad", "Michael", "AMD", "Bloomberg", "Osmantic", "ODS", "Akash", "Tenstorrent",
  "Tacconelli", "Angelo", "Sorellina", "Divine Lorraine", "Juggernaut", "Hulk", "Wes",
  "szed0", "sricursion", "Hoang", "azilber", "McKinsey", "Philly", "Philadelphia",
];

test("trial-journey corpus validates against its schema and binds the transcript identity", async () => {
  const corpus = JSON.parse(await readFile(corpusUrl, "utf8"));
  const schema = JSON.parse(await readFile(schemaUrl, "utf8"));

  assert.equal(corpus.$schema, schema.$id);
  assert.equal(corpus.operation, "pixel-portal-user-trial-journeys");
  assert.match(corpus.sourceTranscriptSha256, /^[a-f0-9]{64}$/);
  assert.equal(corpus.sourceTranscriptBytes, 82247);

  // Structural invariants the schema encodes, checked directly (no external validator dependency).
  assert.ok(corpus.behavioralContract.length >= 8);
  const contractIds = new Set(corpus.behavioralContract.map((c) => c.id));
  assert.equal(contractIds.size, corpus.behavioralContract.length, "contract ids must be unique");
  assert.ok(corpus.journeys.length >= 10, "the trial yields at least ten distinct journeys");

  const categories = new Set(schema.properties.journeys.items.properties.category.enum);
  const profiles = new Set(schema.properties.journeys.items.properties.profile.enum);
  const effects = new Set(schema.properties.journeys.items.properties.effectBoundary.enum);
  const verifiabilities = new Set(schema.properties.journeys.items.properties.verifiability.enum);
  const ids = new Set();
  for (const j of corpus.journeys) {
    assert.ok(!ids.has(j.id), `duplicate journey id ${j.id}`);
    ids.add(j.id);
    assert.ok(categories.has(j.category), `bad category ${j.category}`);
    assert.ok(profiles.has(j.profile), `bad profile ${j.profile}`);
    assert.ok(effects.has(j.effectBoundary), `bad effect ${j.effectBoundary}`);
    assert.ok(verifiabilities.has(j.verifiability), `bad verifiability ${j.verifiability}`);
    // Every asserted contract rule must exist in the behavioral contract.
    for (const a of j.contractAssertions) {
      assert.ok(contractIds.has(a), `journey ${j.id} references unknown contract rule ${a}`);
    }
  }
});

test("the sanitized corpus reproduces no private transcript content", async () => {
  const text = await readFile(corpusUrl, "utf8");
  for (const token of PERSONAL_DENYLIST) {
    assert.ok(!text.includes(token), `sanitization failed: private token "${token}" present in the committed corpus`);
  }
  const corpus = JSON.parse(text);
  assert.equal(corpus.privacy.transcriptContentIncluded, false);
  assert.equal(corpus.privacy.personalDataIncluded, false);
  assert.equal(corpus.privacy.sourcePathIncluded, false);
});

test("the trial corpus captures the load-bearing safety and honesty guardrails", async () => {
  const corpus = JSON.parse(await readFile(corpusUrl, "utf8"));
  const contractIds = new Set(corpus.behavioralContract.map((c) => c.id));
  // The trial's defining guardrails must all be present in the contract.
  for (const required of ["no-self-fork", "never-fabricate", "read-only-without-go", "probe-not-theorize", "honest-unverifiable-edges", "cite-sources-multi"]) {
    assert.ok(contractIds.has(required), `behavioral contract is missing the ${required} guardrail`);
  }
  // The self-fork guardrail must be exercised by the replica-build journey.
  const replica = corpus.journeys.find((j) => j.id === "self-replica-build-request");
  assert.ok(replica, "the self-replica build journey is missing");
  assert.ok(replica.contractAssertions.includes("no-self-fork"), "replica journey must assert the no-self-fork guardrail");
  // At least one journey per honesty-critical category is present.
  const cats = new Set(corpus.journeys.map((j) => j.category));
  for (const c of ["deep-research", "agentic-execution", "guardrail", "capability-honesty"]) {
    assert.ok(cats.has(c), `no journey covers the ${c} category`);
  }
});
