"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");
const vm = require("node:vm");
const {
  patchClientChunk, ORIGINAL, CALL, PRELUDE, KNOWN_CHUNK, KNOWN_SHA256,
} = require("../extensions/services/perplexica/patch-client-citations");

const source = [
  { metadata: { url: "https://example.test/one" } },
  { metadata: { url: "https://example.test/two?a=1&b=2" } },
];

function withTemp(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ods-vane-citations-"));
  try { return run(root); } finally { fs.rmSync(root, { recursive: true, force: true }); }
}

function fixture() {
  return `"use strict";function render(s,l){let d=/\\[(\\d+)\\]/g;${ORIGINAL};return s}`;
}

function fixtureTrust(content) {
  return { "sample.js": crypto.createHash("sha256").update(content).digest("hex") };
}

function embeddedRenderer(patched) {
  assert.ok(patched.startsWith('"use strict";' + PRELUDE));
  assert.equal(patched.split(CALL).length - 1, 1);
  const context = { self: {} };
  vm.runInNewContext(patched, context, { timeout: 1000 });
  return context.self.__odsVaneCitationRender20261003;
}

function checkCases(render) {
  const cited = (n, url = source[n - 1].metadata.url) => `<citation href="${url}">${n}</citation>`;
  const many = Array.from({ length: 18 }, (_, i) => ({ metadata: { url: `https://example.test/s${i + 1}` } }));
  const cm = (n) => `<citation href="https://example.test/s${n}">${n}</citation>`;
  assert.equal(render("A [2][18] B [3][9].", many), `A ${cm(2)}${cm(18)} B ${cm(3)}${cm(9)}.`);
  assert.equal(render("[1][2][3] [1,2][3,4]", many), `${cm(1)}${cm(2)}${cm(3)} ${cm(1)}${cm(2)}${cm(3)}${cm(4)}`);
  assert.equal(render("[2][99] [99][2] [0][2]", many), `${cm(2)}[99] [99]${cm(2)} [0]${cm(2)}`);
  assert.equal(render("[1][2]", []), "");
  const defined = "See [2][18].\n\n[18]: https://x.test";
  assert.equal(render(defined, many), defined);
  const quoted = "> [18]: https://x.test\n\nSee [2][18].";
  assert.equal(render(quoted, many), quoted);
  assert.equal(render("[2][ref] ![2][18] \\[2][18]", many), "[2][ref] ![2][18] \\[2][18]");
  assert.equal(render("[2][18](https://x.test)", many), `${cm(2)}[18](https://x.test)`);
  for (const code of [
    "```\n[18]: https://x.test\n```",
    "~~~\n[18]: https://x.test\n~~~",
    "- ```\n  [18]: https://x.test\n  ```",
    "    [18]: https://x.test",
    ">     [18]: https://x.test",
    "`[18]: https://x.test`",
    "Use ``code\n[18]: https://x.test\ncode``",
  ]) {
    assert.equal(render(code + "\nSee [2][18].", many), code + `\nSee ${cm(2)}${cm(18)}.`);
  }
  assert.equal(render("`[2][18]`\n```\n[2][18]\n```", many), "`[2][18]`\n```\n[2][18]\n```");
  assert.equal(render("See [1] and [1,2].", source), `See ${cited(1)} and ${cited(1)}${cited(2, "https://example.test/two?a=1&amp;b=2")}.`);
  assert.equal(render("See [1, 2].", source), `See ${cited(1)}${cited(2, "https://example.test/two?a=1&amp;b=2")}.`);
  assert.equal(render("```python\nx = [1,2]\n```\nSee [1].", source), `\`\`\`python\nx = [1,2]\n\`\`\`\nSee ${cited(1)}.`);
  assert.equal(render("~~~python\nx = [1]\n~~~\nSee [1].", source), `~~~python\nx = [1]\n~~~\nSee ${cited(1)}.`);
  assert.equal(render("- ~~~python\n  a = [1]\n  ~~~\nSee [1].", source), `- ~~~python\n  a = [1]\n  ~~~\nSee ${cited(1)}.`);
  assert.equal(render("1. ```python\n   a = [1]\n   ```\nSee [1].", source), `1. \`\`\`python\n   a = [1]\n   \`\`\`\nSee ${cited(1)}.`);
  assert.equal(render("- ```python\n  a = [1]", source), "- ```python\n  a = [1]");
  assert.equal(render("```python\nx = [1]", source), "```python\nx = [1]");
  assert.equal(render("Use `[1]` and ``[1,2]``. See [1].", source), `Use \`[1]\` and \`\`[1,2]\`\`. See ${cited(1)}.`);
  assert.equal(render("Use `[1]\n[2]` then [1].", source), `Use \`[1]\n[2]\` then ${cited(1)}.`);
  assert.equal(render(">     values = [1, 2]", source), ">     values = [1, 2]");
  assert.equal(render("An escaped \\` tick; fact [1].", source), "An escaped \\` tick; fact " + cited(1) + ".");
  assert.equal(render("One ` unmatched tick; fact [1].", source), "One ` unmatched tick; fact " + cited(1) + ".");
  assert.equal(render("[a [b] c](https://x.test) [1 [2]]", source), "[a [b] c](https://x.test) [1 [2]]");
  assert.equal(render("\\[1\\] [1](https://x.test/a) [2][ref] ![1] [1]: ref", source), "\\[1\\] [1](https://x.test/a) [2][ref] ![1] [1]: ref");
  assert.equal(render("[label] [0] [-1] [1.5] [1,3] [9]", source), "[label] [0] [-1] [1.5] [1,3] [9]");
  assert.equal(render("[1,2]", [source[0], { metadata: {} }]), "[1,2]");
  assert.equal(render("See [1] but `x=[1]` and [1,2].", []), "See  but `x=[1]` and [1,2].");
  assert.equal(render("[1]", [{ metadata: { url: "javascript:alert(1)" } }]), "[1]");
  assert.equal(render("```python\nx = [1]\n```", []), "```python\nx = [1]\n```");
}

test("pinned Vane expression corrupts fenced code; patched embedded renderer preserves it", () => {
  withTemp((root) => {
    const file = path.join(root, "sample.js");
    const original = fixture();
    fs.writeFileSync(file, original);
    const upstream = vm.runInNewContext(`${original};render`, {});
    assert.match(upstream("```python\nx = [1,2]\n```", source), /<citation href=/);

    assert.throws(() => patchClientChunk(root), /unqualified/);
    const trusted = fixtureTrust(original);
    assert.equal(patchClientChunk(root, trusted).changed, true);
    const patched = fs.readFileSync(file, "utf8");
    assert.equal(patched.split(ORIGINAL).length - 1, 0);
    checkCases(embeddedRenderer(patched));
    assert.equal(spawnSync(process.execPath, ["--check", file]).status, 0);
    assert.equal(patchClientChunk(root, trusted).changed, false);
    assert.equal(fs.readFileSync(file, "utf8"), patched);
    const previousPrelude = PRELUDE.replace("return walk(false);", "return String(message);");
    assert.notEqual(previousPrelude, PRELUDE);
    fs.writeFileSync(file, patched.replace(PRELUDE, previousPrelude));
    assert.equal(patchClientChunk(root, trusted).changed, true);
    assert.equal(fs.readFileSync(file, "utf8"), patched);
    fs.appendFileSync(file, "/* partial or modified bundle */");
    assert.throws(() => patchClientChunk(root, trusted), /patched Vane client hash mismatch/);
  });
});

test("patched Vane client rejects missing or duplicate prelude markers", () => {
  withTemp((root) => {
    const file = path.join(root, "sample.js");
    const original = fixture();
    fs.writeFileSync(file, original);
    const trusted = fixtureTrust(original);
    patchClientChunk(root, trusted);
    const patched = fs.readFileSync(file, "utf8");
    const marker = "/* ods-vane-citation-prelude-end:20261003 */\n";
    assert.ok(patched.includes(marker));
    fs.writeFileSync(file, patched.replace(marker, ""));
    assert.throws(() => patchClientChunk(root, trusted), /unknown or partial shape/);
    fs.writeFileSync(file, patched.replace(marker, marker + marker));
    assert.throws(() => patchClientChunk(root, trusted), /unknown or partial shape/);
  });
});

test("unknown, duplicate, and partial client chunks stop the patch", () => {
  for (const content of ["unknown bundle", `${ORIGINAL}${ORIGINAL}`, `self.__odsVaneCitationRender20261003=bad;${CALL}`]) {
    withTemp((root) => {
      fs.writeFileSync(path.join(root, "sample.js"), content);
      assert.throws(() => patchClientChunk(root, fixtureTrust(content)), content === "unknown bundle" ? /expected one/ : /unknown or partial/);
    });
  }
  withTemp((root) => {
    fs.writeFileSync(path.join(root, "a.js"), fixture());
    fs.writeFileSync(path.join(root, "b.js"), fixture());
    assert.throws(() => patchClientChunk(root), /expected one/);
  });
  withTemp((root) => {
    fs.writeFileSync(path.join(root, KNOWN_CHUNK), fixture());
    assert.throws(() => patchClientChunk(root), /hash mismatch/);
  });
});

const authentic = process.env.ODS_VANE_AUTHENTIC_CHUNK || path.join(__dirname, "fixtures", "vane-v1.12.2-1220-5cd2adbf287bf784.js");
test("captured pinned image chunk has the same live behavior and patches cleanly", () => {
  const bytes = fs.readFileSync(authentic);
  assert.equal(bytes.length, 49892);
  assert.equal(crypto.createHash("sha256").update(bytes).digest("hex"), KNOWN_SHA256);
  withTemp((root) => {
    const file = path.join(root, KNOWN_CHUNK);
    fs.writeFileSync(file, bytes);
    assert.equal(patchClientChunk(root).changed, true);
    const patched = fs.readFileSync(file, "utf8");
    checkCases(embeddedRenderer(patched));
    assert.equal(spawnSync(process.execPath, ["--check", file]).status, 0);
    assert.equal(patchClientChunk(root).changed, false);
  });
});
