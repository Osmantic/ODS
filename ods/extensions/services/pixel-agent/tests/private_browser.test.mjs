import test from "node:test";
import assert from "node:assert/strict";
import { createToolLoopGuard, privateBrowserAccessForAgent } from "../plugin/tool-loop-guard.mjs";
import { promptContractForAgent, ODS_PRIVATE_BROWSER_CONTRACT, ODS_PRIVATE_URL_CONTRACT } from "../plugin/prompt-contract.mjs";

const config = () => ({
  browser: { enabled: true },
  plugins: { allow: ["browser"], entries: { browser: { enabled: true } } },
  tools: { alsoAllow: ["browser"], sandbox: { tools: { allow: ["browser", "read"] } } },
  agents: { defaults: { sandbox: { mode: "all" } }, list: [{ id: "pixel", sandbox: { browser: { allowHostControl: true } } }] },
});
const context = { agentId: "pixel", runId: "browser-run", sessionId: "browser-session" };
const event = { messages: [{ role: "user", content: "Use the browser tool to open http://127.0.0.1:3001/pixel-preview/site-test/ and click Pause. Do not edit files." }] };
const browser = { toolName: "tool_call", params: { id: "browser", args: { action: "open", target: "host", url: "http://127.0.0.1:3001/pixel-preview/site-test/" } } };

test("private browser access comes from the configured agent capability", () => {
  assert.equal(privateBrowserAccessForAgent(config()), true);
  assert.equal(privateBrowserAccessForAgent(undefined), false);
  for (const change of [
    c => { c.browser.enabled = false; },
    c => { c.plugins.allow = ["pixel-ods"]; },
    c => { c.plugins.entries.browser.enabled = false; },
    c => { c.agents.list[0].sandbox.browser.allowHostControl = false; },
    c => { c.tools.sandbox.tools.allow = ["read"]; },
    c => { c.tools.deny = ["browser"]; },
  ]) { const c = config(); change(c); assert.equal(privateBrowserAccessForAgent(c), false); }
  assert.equal(privateBrowserAccessForAgent(config(), "other-agent"), false);
});

test("configured browser handles owner private pages without a global tool denial", () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context, "pixel", event, { privateBrowserAccess: true });
  assert.equal(guard.beforeToolCall(browser, context)?.block, undefined);
  const text = JSON.stringify(promptContractForAgent(context, "pixel", event, { privateBrowserAccess: true }));
  assert.ok(text.includes(ODS_PRIVATE_BROWSER_CONTRACT));
  assert.ok(!text.includes(ODS_PRIVATE_URL_CONTRACT));
});

test("absent browser retains the private-page boundary", () => {
  const guard = createToolLoopGuard(); guard.observeRun(context, "pixel", event);
  assert.equal(guard.beforeToolCall(browser, context)?.block, true);
  assert.ok(JSON.stringify(promptContractForAgent(context, "pixel", event)).includes(ODS_PRIVATE_URL_CONTRACT));
});

test("public fetch stays blocked but can pivot once to the configured browser", () => {
  const guard = createToolLoopGuard(); guard.observeRun(context, "pixel", event, { privateBrowserAccess: true });
  const fetch = { toolName: "web_fetch", params: { url: "http://127.0.0.1:3001/" } };
  assert.equal(guard.beforeToolCall(fetch, context)?.block, true);
  assert.equal(guard.beforeToolCall(browser, context)?.block, undefined);
  assert.equal(guard.beforeToolCall(fetch, context)?.block, true);
  assert.equal(guard.beforeToolCall(browser, context)?.block, true);
});

test("untrusted private fetch targets do not gain a browser redirect from a public task", () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context, "pixel", { prompt: "Read https://example.com and summarize it." }, { privateBrowserAccess: true });
  assert.equal(guard.beforeToolCall({ toolName: "web_fetch", params: { url: "http://127.0.0.1:3001/" } }, context)?.block, true);
  assert.equal(guard.beforeToolCall(browser, context)?.block, true);
});

test("a denied private fetch can recover to a public browser destination", () => {
  for (const key of ["url", "targetUrl"]) {
    const guard = createToolLoopGuard();
    guard.observeRun(context, "pixel", {prompt: "Read public documentation."}, {privateBrowserAccess: true});
    assert.equal(guard.beforeToolCall({toolName: "web_fetch", params: {url: "http://127.0.0.1/"}}, context)?.block, true);
    assert.equal(guard.beforeToolCall({toolName: "browser", params: {action: "open", [key]: "https://docs.python.org/3/"}}, context)?.block, undefined);
    assert.equal(guard.beforeToolCall({toolName: "browser", params: {action: "open", [key]: "http://127.0.0.1/"}}, context)?.block, true);
  }
});

test("browser navigation remains subject to configured browser policy rather than an extra text classifier", () => {
  for (const key of ["url", "targetUrl"]) {
    for (const wrapped of [false, true]) {
      const guard = createToolLoopGuard();
      guard.observeRun(context, "pixel", { prompt: "Read https://example.com." }, { privateBrowserAccess: true });
      const params = { action: "open", [key]: "http://127.0.0.1:3001/" };
      const call = wrapped
        ? { toolName: "tool_call", params: { id: "openclaw:browser:browser", args: params } }
        : { toolName: "browser", params };
      assert.equal(guard.beforeToolCall(call, context)?.block, undefined);
      const publicCall = { toolName: "browser", params: { action: "open", [key]: "https://example.com/" } };
      assert.equal(guard.beforeToolCall(publicCall, context)?.block, undefined);
    }
  }
});

test("configured browser permits natural follow-up navigation and URL-free actions", () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context, "pixel", event, { privateBrowserAccess: true });
  assert.equal(guard.beforeToolCall({ toolName: "browser", params: { action: "navigate", targetUrl: "http://127.0.0.1:3001/" } }, context)?.block, undefined);
  const followup = { ...context, runId: "browser-followup" };
  guard.observeRun(followup, "pixel", { prompt: "Close and reopen that preview." }, { privateBrowserAccess: true });
  assert.equal(guard.beforeToolCall(browser, followup)?.block, undefined);
  assert.equal(guard.beforeToolCall({ toolName: "browser", params: { action: "navigate", targetUrl: "http://127.0.0.1:3001/" } }, followup)?.block, undefined);
  for (const action of ["snapshot", "screenshot", "console"]) {
    assert.equal(guard.beforeToolCall({ toolName: "browser", params: { action, targetId: "test-tab" } }, followup)?.block, undefined);
  }
});

test("wrapped public fetch tools retain their private-address restrictions", () => {
  const guard = createToolLoopGuard();
  guard.observeRun(context, "pixel", event, { privateBrowserAccess: true });
  const fetch = { toolName: "tool_call", params: { id: "openclaw:web_fetch:web_fetch", args: { url: "http://127.0.0.1/" } } };
  assert.equal(guard.beforeToolCall(fetch, context)?.block, true);
  assert.equal(guard.beforeToolCall(browser, context)?.block, undefined);
});
