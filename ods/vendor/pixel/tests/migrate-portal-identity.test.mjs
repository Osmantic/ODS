import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const script = join(root, "scripts/migrate-portal-identity.mjs");
const source = join(root, "workspace-template");
const oldSoul = `# Pixel

You are Pixel, a private chief of staff and technical partner running on infrastructure controlled by the owner.

Your style is calm, candid, curious, and concise. Make ambiguity visible, form useful opinions, and explain tradeoffs without theatrics. Protect the owner's attention and privacy. Prefer reversible actions and leave an audit trail for consequential changes.

You may help with research, planning, inbox review, scheduling, technical work, and maintaining useful private context. Access is not authorization: reading a system does not permit changing it, and the ability to change it does not permit doing so without explicit owner approval.
`;

async function fixture() {
  const temporary = await mkdtemp(join(tmpdir(), "ods-portal-identity-test-"));
  const workspace = join(temporary, "workspace");
  const generated = join(temporary, "generated");
  await mkdir(workspace);
  await mkdir(generated);
  const identity = (await readFile(join(source, "IDENTITY.md"), "utf8"))
    .replaceAll("{{OWNER_NAME}}", "Owner")
    .replaceAll("{{ORGANIZATION}}", "Organization")
    .replaceAll("{{TIME_ZONE}}", "UTC")
    .replaceAll("{{DEPLOYMENT_NAME}}", "Test");
  await writeFile(join(generated, "SOUL.md"), await readFile(join(source, "SOUL.md")));
  await writeFile(join(generated, "IDENTITY.md"), identity);
  return { temporary, workspace, generated, identity };
}

function run(workspace, generated) {
  return execFileSync(process.execPath, [script, workspace, generated], { encoding: "utf8" });
}

test("upgrades only exact legacy defaults and remains idempotent", async () => {
  const state = await fixture();
  try {
    await writeFile(join(state.workspace, "SOUL.md"), oldSoul);
    await writeFile(join(state.workspace, "IDENTITY.md"), state.identity.replace("- Name: Portal\n", "- Name: Pixel\n"));
    assert.match(run(state.workspace, state.generated), /SOUL.md migrated; IDENTITY.md migrated/);
    assert.equal(await readFile(join(state.workspace, "SOUL.md"), "utf8"),
      await readFile(join(state.generated, "SOUL.md"), "utf8"));
    assert.equal(await readFile(join(state.workspace, "IDENTITY.md"), "utf8"), state.identity);
    assert.match(run(state.workspace, state.generated), /SOUL.md current; IDENTITY.md current/);
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("preserves owner-edited profile files", async () => {
  const state = await fixture();
  try {
    const customSoul = oldSoul + "\nOwner's chosen tone.\n";
    const customIdentity = state.identity.replace("- Name: Portal\n", "- Name: My Assistant\n");
    await writeFile(join(state.workspace, "SOUL.md"), customSoul);
    await writeFile(join(state.workspace, "IDENTITY.md"), customIdentity);
    assert.match(run(state.workspace, state.generated), /SOUL.md owner-customized; IDENTITY.md owner-customized/);
    assert.equal(await readFile(join(state.workspace, "SOUL.md"), "utf8"), customSoul);
    assert.equal(await readFile(join(state.workspace, "IDENTITY.md"), "utf8"), customIdentity);
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});
