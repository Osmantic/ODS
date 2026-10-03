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
  const many = Array.from({ length: 28 }, (_, i) => ({ metadata: { url: `https://example.test/${i + 1}` } }));
  const manyCited = (n) => `<citation href="https://example.test/${n}">${n}</citation>`;
  const inline = (value) => `<span class="not-prose"><code style="font-family:monospace;padding:0 .2em;border-radius:.2em;background-color:rgba(127,127,127,.14)">${value}</code></span>`;
  assert.equal(render("See [1] and [1,2].", source), `See ${cited(1)} and ${cited(1)}${cited(2, "https://example.test/two?a=1&amp;b=2")}.`);
  assert.equal(render("See [1, 2].", source), `See ${cited(1)}${cited(2, "https://example.test/two?a=1&amp;b=2")}.`);
  assert.equal(render("Evidence [12][27] and [13][21].", many),
    `Evidence ${manyCited(12)}${manyCited(27)} and ${manyCited(13)}${manyCited(21)}.`);
  assert.equal(render("Evidence [12,13][27].", many),
    `Evidence ${manyCited(12)}${manyCited(13)}${manyCited(27)}.`);
  assert.equal(render("Evidence [12][27][13].", many),
    `Evidence ${manyCited(12)}${manyCited(27)}${manyCited(13)}.`);
  assert.equal(render("[12][27]", many.map((item, i) => i === 26 ? { metadata: { url: "javascript:alert(1)" } } : item)),
    "[12][27]");
  assert.equal(render("[1][2]\n\n[2]: https://example.test/reference", many),
    "[1][2]\n\n[2]: https://example.test/reference");
  assert.equal(render("Evidence [12][27].\n\n```text\n[27]: https://example.test/not-a-reference\n```", many),
    `Evidence ${manyCited(12)}${manyCited(27)}.\n\n\`\`\`text\n[27]: https://example.test/not-a-reference\n\`\`\``);
  assert.equal(render("Evidence [12][27].\n\n    [27]: https://example.test/not-a-reference", many),
    `Evidence ${manyCited(12)}${manyCited(27)}.\n\n    [27]: https://example.test/not-a-reference`);
  assert.equal(render("Evidence [12][27].\n\n[27]:", many),
    `Evidence ${manyCited(12)}${manyCited(27)}.\n\n[27]:`);
  assert.equal(render("Evidence [12][27].\n\n[27]:\n https://example.test/reference", many),
    "Evidence [12][27].\n\n[27]:\n https://example.test/reference");
  assert.equal(render("Evidence [12][27].\n\n<!--\n[27]: https://example.test/not-a-reference\n-->", many),
    `Evidence ${manyCited(12)}${manyCited(27)}.\n\n<!--\n[27]: https://example.test/not-a-reference\n-->`);
  assert.equal(render("Evidence [12][27].\n\nText <!--\n[27]: https://example.test/not-a-reference\n-->", many),
    `Evidence ${manyCited(12)}${manyCited(27)}.\n\nText <!--\n[27]: https://example.test/not-a-reference\n-->`);
  assert.equal(render("Evidence [12][27].\n\nUse `<!--` literally.\n\n[27]: https://example.test/reference", many),
    `Evidence [12][27].\n\nUse ${inline("&lt;!--")} literally.\n\n[27]: https://example.test/reference`);
  assert.equal(render("Evidence [12][27].\n\n[27]:\n```text\ncode\n```", many),
    `Evidence ${manyCited(12)}${manyCited(27)}.\n\n[27]:\n\`\`\`text\ncode\n\`\`\``);
  assert.equal(render("- [12][27]\n\n    [27]: https://example.test/reference", many),
    "- [12][27]\n\n    [27]: https://example.test/reference");
  for (const prefix of ["- > ", "- - ", "- > - > ", "> - > - ", "1. > ", "- > ".repeat(8)]) {
    const markdown = `[12][27]\n\n${prefix}[27]: https://example.test/reference`;
    assert.equal(render(markdown, many), markdown);
  }
  assert.equal(render("[12][27]\n\n- > [28]: https://example.test/reference", many),
    `${manyCited(12)}${manyCited(27)}\n\n- > [28]: https://example.test/reference`);
  assert.equal(render("![12][27] and \\[12][27]", many), "![12][27] and \\[12][27]");
  assert.equal(render("[12](https://x.test) [27]", many),
    `[12](https://x.test) ${manyCited(27)}`);
  assert.equal(render("Use `[12][27]` and [12][27].", many),
    `Use ${inline("[12][27]")} and ${manyCited(12)}${manyCited(27)}.`);
  assert.equal(render("```python\nx = [1,2]\n```\nSee [1].", source), `\`\`\`python\nx = [1,2]\n\`\`\`\nSee ${cited(1)}.`);
  assert.equal(render("~~~python\nx = [1]\n~~~\nSee [1].", source), `~~~python\nx = [1]\n~~~\nSee ${cited(1)}.`);
  assert.equal(render("- ~~~python\n  a = [1]\n  ~~~\nSee [1].", source), `- ~~~python\n  a = [1]\n  ~~~\nSee ${cited(1)}.`);
  assert.equal(render("1. ```python\n   a = [1]\n   ```\nSee [1].", source), `1. \`\`\`python\n   a = [1]\n   \`\`\`\nSee ${cited(1)}.`);
  assert.equal(render("- ```python\n  a = [1]", source), "- ```python\n  a = [1]");
  assert.equal(render("```python\nx = [1]", source), "```python\nx = [1]");
  assert.equal(render("Use `[1]` and ``[1,2]``. See [1].", source), `Use ${inline("[1]")} and ${inline("[1,2]")}. See ${cited(1)}.`);
  assert.equal(render("Use ``a ` b`` now.", source), `Use ${inline("a ` b")} now.`);
  assert.equal(render("Use `x < 2 & \"hi\" 'x'`.", source),
    `Use ${inline("x &lt; 2 &amp; &quot;hi&quot; &#39;x&#39;")}.`);
  assert.equal(render("The Python `venv` module uses `python -m venv <environment_name>`. See [1].", source),
    `The Python ${inline("venv")} module uses ${inline("python -m venv &lt;environment_name&gt;")}. See ${cited(1)}.`);
  assert.equal(render("Use ``<environment_name>`` with `venv`.", source),
    `Use ${inline("&lt;environment_name&gt;")} with ${inline("venv")}.`);
  assert.equal(render("Use `[1]\n[2]` then [1].", source), `Use \`[1]\n[2]\` then ${cited(1)}.`);
  assert.equal(render("-     `x`", source), "-     `x`");
  assert.equal(render("1.     `x`", source), "1.     `x`");
  assert.equal(render(">     values = [1, 2]", source), ">     values = [1, 2]");
  assert.equal(render("An escaped \\` tick; fact [1].", source), "An escaped \\` tick; fact " + cited(1) + ".");
  assert.equal(render("One ` unmatched tick; fact [1].", source), "One ` unmatched tick; fact " + cited(1) + ".");
  assert.equal(render("[a [b] c](https://x.test) [1 [2]]", source), "[a [b] c](https://x.test) [1 [2]]");
  assert.equal(render("[label `code`](https://x.test) and `ok`", source),
    "[label `code`](https://x.test) and " + inline("ok"));
  assert.equal(render("<code>x</code> See [1] and `ok`", source),
    "<code>x</code> See " + cited(1) + " and `ok`");
  assert.equal(render("<span>Use `python -m venv <env>`</span> See [1]", source),
    "<span>Use `python -m venv <env>`</span> See " + cited(1));
  assert.equal(render("Escaped \\`<span>\\` leaves `venv` literal; see [1].", source),
    "Escaped \\`<span>\\` leaves `venv` literal; see " + cited(1) + ".");
  assert.equal(render("<img src=x> See [1]", source), "<img src=x> See " + cited(1));
  assert.equal(render('<a title="\nfoo > bar\n`x`\n"> See [1]', source),
    '<a title="\nfoo > bar\n`x`\n"> See ' + cited(1));
  assert.equal(render("Use `early`.\n<pre>x</pre> See [1]", source),
    "Use `early`.\n<pre>x</pre> See " + cited(1));
  assert.equal(render("<!-- raw --> Use `ok` [1]", source),
    "<!-- raw --> Use `ok` " + cited(1));
  assert.equal(render("<!DOCTYPE html>\nUse `ok` [1]", source),
    "<!DOCTYPE html>\nUse `ok` " + cited(1));
  assert.equal(render("\\[1\\] [1](https://x.test/a) [2][ref] ![1] [1]: ref", source), "\\[1\\] [1](https://x.test/a) [2][ref] ![1] [1]: ref");
  assert.equal(render("[label] [0] [-1] [1.5] [1,3] [9]", source), "[label] [0] [-1] [1.5] [1,3] [9]");
  assert.equal(render("[1,2]", [source[0], { metadata: {} }]), "[1,2]");
  assert.equal(render("See [1] but `x=[1]` and [1,2].", []), `See  but ${inline("x=[1]")} and [1,2].`);
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
    const previousPrelude = PRELUDE.replace("return prose(line, lineBase);", "return String(line);");
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
