import assert from "node:assert/strict";
import { after, test } from "node:test";
import {
  chmodSync,
  linkSync,
  lstatSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { createEvidenceArtifactWriter } from "../plugin/evidence-artifact.mjs";

const roots = [];
after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
});

function workspace() {
  const root = mkdtempSync(path.join(tmpdir(), "pixel-evidence-artifact-"));
  roots.push(root);
  return root;
}

test("writes and verifies deterministic evidence through one owned file descriptor", () => {
  const root = workspace();
  const writeEvidence = createEvidenceArtifactWriter({ workspaceRoot: root });
  const content = "verified receipt-bound evidence\n";
  const result = writeEvidence({ relativePath: "reports/machine.txt", content });

  assert.equal(result.relativePath, "reports/machine.txt");
  assert.equal(result.bytes, Buffer.byteLength(content));
  assert.equal(result.readbackVerified, true);
  assert.match(result.sha256, /^[a-f0-9]{64}$/);
  assert.equal(readFileSync(path.join(root, "reports", "machine.txt"), "utf8"), content);
  if (process.platform !== "win32") {
    assert.equal(lstatSync(path.join(root, "reports", "machine.txt")).mode & 0o777, 0o600);
  }
});

test("rejects traversal and unsafe workspace parents", () => {
  const root = workspace();
  const writeEvidence = createEvidenceArtifactWriter({ workspaceRoot: root });
  assert.throws(
    () => writeEvidence({ relativePath: "../outside.txt", content: "evidence\n" }),
    /invalid Pixel evidence path/
  );

  if (process.platform !== "win32") {
    const outside = workspace();
    symlinkSync(outside, path.join(root, "linked"), "dir");
    assert.throws(
      () => writeEvidence({ relativePath: "linked/report.txt", content: "evidence\n" }),
      /unsafe Pixel workspace directory/
    );
  }
});

test("refuses to overwrite a multiply-linked destination", () => {
  const root = workspace();
  mkdirSync(path.join(root, "reports"), { mode: 0o700 });
  chmodSync(path.join(root, "reports"), 0o700);
  const first = path.join(root, "reports", "first.txt");
  writeFileSync(first, "keep\n", { mode: 0o600 });
  linkSync(first, path.join(root, "reports", "report.txt"));

  const writeEvidence = createEvidenceArtifactWriter({ workspaceRoot: root });
  assert.throws(
    () => writeEvidence({ relativePath: "reports/report.txt", content: "replace\n" }),
    /unsafe Pixel evidence destination/
  );
  assert.equal(readFileSync(first, "utf8"), "keep\n");
  assert.equal(readFileSync(path.join(root, "reports", "report.txt"), "utf8"), "keep\n");
});
