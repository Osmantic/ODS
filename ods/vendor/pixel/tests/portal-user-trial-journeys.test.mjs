import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const corpusUrl = new URL("../security-evals/portal-user-trial/trial-journeys-v1.json", import.meta.url);
const schemaUrl = new URL("../schemas/portal-user-trial-journey-v1.schema.json", import.meta.url);

// The private transcript must never be reproduced in the committed corpus. This denylist holds
// tokens that appear in the real trial content (people, organizations, places, private detail).
// If any surfaces in the sanitized corpus, sanitization has failed and the test blocks it.
// Only [length, SHA-256(salt + token)] is kept so the list does not itself publish those
// details; any case-sensitive substring of the corpus that hashes to an entry fails.
const DENYLIST_SALT = "pixel-portal-trial-denylist-v1:";
const PERSONAL_DENYLIST = [
  [3, "1c93db696101a053e43965da728d862d504d02c7d460f30524e528c277e36634"],
  [3, "9acbbec358fbfcb5b28cf1ddcde66065ad0a95f7f2fffad4e49a7a575429839a"],
  [3, "d8d90d6963738e19db2b2451fd3229aa17f39a95d425c73c5d1c5e98d0aee880"],
  [4, "506c0b217fbfe65b57ef44ef7f95774c60fdaf80749bacae819ea30e9a310302"],
  [5, "0cffe88071252a76597961d592e77e28e5df020dc170affbb7743c421bb9e4c1"],
  [5, "2f930fd3e4a351949db98a650339a7b2b07c86dae4125fe26e41e75328cf97c2"],
  [5, "615e37e6ac7f6980760becdc2489a1271b8ff0a32ed2cb6965e3a01912357822"],
  [5, "d2f4d3e6adaee09e47b3d1c69313cb23eefa214baa1753ff2820a0979fd84b88"],
  [5, "e24c90a06612c8c818b8a7d83d0853a8a2c10f404718c7accf846c41406394a8"],
  [6, "0b908ff801038474914552cc950232836f6c74d9398c943c0eb15c958672aff9"],
  [6, "1966159e25d1448ffe3aa9ed08adcf2754a3cfe46faf40faf991d55205cd8c9e"],
  [7, "8f4e00ad790f8a86fe27bfc347bb5fefabee62372af04f5da9045c8a821f6e85"],
  [7, "99d40eeaf0f685a6e32b9a3121b7303d2d135866f52b720c0f7d2e00ef4252eb"],
  [8, "c220459d5e3a910b9c02dbeb32c2a6b6e1cc3385e9b24420b6d3c85805219cc2"],
  [8, "debd27cc37c346336f1931272dfd251aaa7d259e9a63684106d7423684fa7dac"],
  [9, "54a766bac21479a6a96e7df7f8aa47cd4ffd0c8b9874bb2ff0a6d7b5dadbf4b8"],
  [9, "9c040584f422b832357bcf35ddd5b3ad0c68d704007943a010f53228e613ac18"],
  [10, "05cd72ee2e26be220093de03db8e75313844f91a69263cfa94f9020873a62872"],
  [10, "4a56fbdde14cca5723e2952db7c6abbdfe95e8b0efbeb5602c07541c6086a52d"],
  [10, "c76ef4da5d412d3dd55b97e6057893e1c70423368c8e17a02a1b925b3615b9d7"],
  [11, "ead92468fa13c91818bb4d4bd5c6df5fb8151f8c3a04399a6b97f6c008ce2a08"],
  [12, "39962b8452c1961b692dc60ddc95e1278834601548d39eeb072841c24e091681"],
  [15, "5e53082b6563ed2191f5d8fefd58e33db52be13b03117dc928264684baca0513"],
];

function denylistedSpans(text, denylist = PERSONAL_DENYLIST) {
  const found = [];
  for (const length of new Set(denylist.map(([size]) => size))) {
    const digests = new Set(denylist.filter(([size]) => size === length).map(([, digest]) => digest));
    for (let offset = 0; offset + length <= text.length; offset += 1) {
      const digest = createHash("sha256").update(DENYLIST_SALT + text.slice(offset, offset + length)).digest("hex");
      if (digests.has(digest)) found.push(`${length} characters at offset ${offset}`);
    }
  }
  return found;
}

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
  assert.deepEqual(denylistedSpans(text), [], "sanitization failed: a private token is present in the committed corpus");
  const sample = [[7, createHash("sha256").update(`${DENYLIST_SALT}Example`).digest("hex")]];
  assert.deepEqual(denylistedSpans('{"note":"an Example token"}', sample), ["7 characters at offset 12"]);
  assert.deepEqual(denylistedSpans('{"note":"an example token"}', sample), [], "matching stays case-sensitive");
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
