// Pixel ODS projection reader contract tests.
//
// Imports projection.mjs only. Covers good status/apps projections, symlink
// rejection, wrong-owner/writable file rejection (via injectable lstat/open),
// oversized files, extra keys, invalid names/status, duplicates, stale/future
// timestamps, replacement-race mismatch, and generic errors.

import test from "node:test";
import assert from "node:assert/strict";
import {
  appsPayload,
  readProjection,
  statusFileFromEnv,
  statusPayload,
} from "../plugin/projection.mjs";

const GOOD_TS = new Date(Date.now() - 1000).toISOString();
const APP_PORTS = {
  dashboard: 3001,
  webui: 3000,
  searxng: 8888,
  perplexica: 3004,
  whisper: 9001,
  tts: 8880,
  n8n: 5678,
  qdrant: 6333,
  embeddings: 8090,
  litellm: 4000,
  llama: 11434,
  privacy_shield: 8085,
  token_spy: 3005,
  ape: 7890,
  hermes_proxy: 9120,
};

function goodProjection(overrides = {}) {
  const projection = {
    schema_version: 2,
    timestamp: GOOD_TS,
    service: "pixel-agent",
    ods_version: "2.6.0",
    ingress_ready: true,
    gateway_reachable: true,
    docker: "ok",
    online_apps: 2,
    app_ports: APP_PORTS,
    runtime: { model: "Qwen3.5-9B-Q4_K_M.gguf", context_length: 32768 },
    apps: [
      { name: "pixel-agent", status: "healthy" },
      { name: "openclaw", status: "running" },
    ],
    ...overrides,
  };
  if (!Object.hasOwn(overrides, "online_apps")) {
    projection.online_apps = projection.apps.filter(
      ({ status }) => status === "healthy" || status === "running"
    ).length;
  }
  return projection;
}

// Build a dependency set backed by in-memory files.
function memoryFs(files) {
  const store = new Map();
  for (const [path, entry] of Object.entries(files)) {
    store.set(path, entry);
  }
  const lstat = async (p) => {
    const entry = store.get(p);
    if (!entry) {
      const err = new Error("ENOENT");
      err.code = "ENOENT";
      throw err;
    }
    return entry.stat;
  };
  const open = async (p) => {
    const entry = store.get(p);
    if (!entry) {
      const err = new Error("ENOENT");
      err.code = "ENOENT";
      throw err;
    }
    if (entry.followTo) return { ...store.get(entry.followTo) };
    return entry;
  };
  return {
    lstat,
    open,
    readFile: async (fd) => fd.raw,
    stat: async (fd) => fd.stat,
    ownerUid: 0,
  };
}

function regularStat({ uid = 0, mode = 0o644, size = 100, mtimeMs = 1000 } = {}) {
  return {
    isFile: () => true,
    isSymbolicLink: () => false,
    isSocket: () => false,
    isDirectory: () => false,
    dev: 1,
    ino: 1,
    uid,
    mode,
    size,
    mtimeMs,
  };
}

function makeEntry(raw, stat = regularStat({ size: Buffer.byteLength(raw) })) {
  return { raw, stat, followTo: null };
}

const FIXED = "/run/ods-pixel/ods-status.json";

function asRejected(promise) {
  return promise.then(
    () => null,
    (err) => err
  );
}

test("reads a good projection and returns a freshly constructed object", async () => {
  const raw = JSON.stringify(goodProjection());
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const out = await readProjection(FIXED, fsImpl, Date.now());
  assert.equal(out.schema_version, 2);
  assert.equal(out.service, "pixel-agent");
  assert.equal(out.ods_version, "2.6.0");
  assert.equal(out.ingress_ready, true);
  assert.equal(out.gateway_reachable, true);
  assert.equal(out.docker, "ok");
  assert.equal(out.app_count, 2);
  assert.equal(out.online_app_count, 2);
  assert.deepEqual(out.app_ports, APP_PORTS);
  assert.deepEqual(out.runtime, {
    model: "Qwen3.5-9B-Q4_K_M.gguf",
    context_length: 32768,
  });
  assert.equal(out.stale, false);
  assert.equal(out.boundary, "status-only");
  assert.equal(out.apps.length, 2);
  assert.deepEqual(out.apps, [
    { name: "pixel-agent", status: "healthy" },
    { name: "openclaw", status: "running" },
  ]);
  // Must be a new object, not the parsed raw value.
  assert.notEqual(out, JSON.parse(raw));
  assert.notEqual(out.apps, JSON.parse(raw).apps);
});

test("tool payloads expose the validated application count explicitly", async () => {
  const raw = JSON.stringify(goodProjection());
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const projection = await readProjection(FIXED, fsImpl, Date.now());
  const status = statusPayload(projection);
  const apps = appsPayload(projection);

  assert.equal(status.app_count, 2);
  assert.equal(status.online_app_count, 2);
  assert.equal(status.ods_version, "2.6.0");
  assert.equal(status.runtime.context_length, 32768);
  assert.equal(Object.hasOwn(status, "apps"), false);
  assert.equal(apps.app_count, 2);
  assert.equal(apps.apps.length, apps.app_count);
  assert.equal(apps.online_app_count, 2);
  assert.equal(apps.apps.length, apps.app_count);
});

test("application payloads expose allowlisted purposes and configured local URLs", async () => {
  const raw = JSON.stringify(goodProjection({
    apps: [
      { name: "ods-n8n", status: "healthy" },
      { name: "ods-whisper", status: "healthy" },
    ],
  }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const projection = await readProjection(FIXED, fsImpl, Date.now());

  assert.deepEqual(appsPayload(projection).apps, [
    {
      name: "ods-n8n",
      status: "healthy",
      display_name: "n8n",
      purpose: "workflow automation",
      url: "http://localhost:5678/",
    },
    {
      name: "ods-whisper",
      status: "healthy",
      display_name: "Whisper",
      purpose: "speech-to-text API",
      url: "http://localhost:9001/docs",
    },
  ]);
});

test("starting application status is accepted", async () => {
  const raw = JSON.stringify(
    goodProjection({
      ingress_ready: false,
      runtime: null,
      apps: [{ name: "searxng", status: "starting" }],
    })
  );
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const out = await readProjection(FIXED, fsImpl, Date.now());
  assert.equal(out.docker, "ok");
  assert.equal(out.ingress_ready, false);
  assert.deepEqual(out.apps, [{
    name: "searxng",
    status: "starting",
    display_name: "SearXNG",
    purpose: "private web search",
    url: "http://localhost:8888/",
  }]);
});

test("stopped deployed applications remain in the total but not the online count", async () => {
  const raw = JSON.stringify(goodProjection({
    apps: [
      { name: "ods-dashboard", status: "healthy" },
      { name: "ods-n8n", status: "stopped" },
    ],
  }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const out = await readProjection(FIXED, fsImpl, Date.now());
  assert.equal(out.app_count, 2);
  assert.equal(out.online_app_count, 1);
  assert.deepEqual(out.apps[1], {
    name: "ods-n8n",
    status: "stopped",
    display_name: "n8n",
    purpose: "workflow automation",
    url: "http://localhost:5678/",
  });
});

test("docker unavailable requires empty applications but preserves selected runtime truth", async () => {
  const raw = JSON.stringify(goodProjection({
    docker: "unavailable",
    runtime: { model: "org/model:variant", context_length: 2_000_000 },
    apps: [],
  }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const out = await readProjection(FIXED, fsImpl, Date.now());
  assert.equal(out.docker, "unavailable");
  assert.equal(out.app_count, 0);
  assert.equal(out.online_app_count, 0);
  assert.deepEqual(out.runtime, {
    model: "org/model:variant",
    context_length: 2_000_000,
  });
});

test("accepts unknown version and unavailable runtime without inventing facts", async () => {
  const raw = JSON.stringify(goodProjection({ ods_version: "unknown", runtime: null }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const out = await readProjection(FIXED, fsImpl, Date.now());
  assert.equal(out.ods_version, "unknown");
  assert.equal(out.runtime, null);
});

test("rejects mismatched online count and malformed runtime or version", async () => {
  for (const bad of [
    goodProjection({ online_apps: 1 }),
    goodProjection({ runtime: { model: "../secret", context_length: 32768 } }),
    goodProjection({ runtime: { model: "model.gguf", context_length: 0 } }),
    goodProjection({ ods_version: "2.6.0\nsecret" }),
    goodProjection({ app_ports: { ...APP_PORTS, n8n: 0 } }),
    goodProjection({ app_ports: { ...APP_PORTS, extra: 1234 } }),
  ]) {
    const fsImpl = memoryFs({ [FIXED]: makeEntry(JSON.stringify(bad)) });
    const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
    assert.ok(err);
    assert.match(err.message, /unavailable/);
  }
});

test("rejects a symlink at the fixed path", async () => {
  const stat = {
    isFile: () => false,
    isSymbolicLink: () => true,
    isSocket: () => false,
    isDirectory: () => false,
    dev: 1,
    ino: 9,
    mode: 0o777,
    size: 10,
    mtimeMs: 1,
  };
  const fsImpl = memoryFs({ [FIXED]: { raw: "", stat, followTo: null } });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects a non-regular path (directory)", async () => {
  const stat = {
    isFile: () => false,
    isSymbolicLink: () => false,
    isSocket: () => false,
    isDirectory: () => true,
    dev: 1,
    ino: 3,
    mode: 0o755,
    size: 0,
    mtimeMs: 1,
  };
  const fsImpl = memoryFs({ [FIXED]: { raw: "", stat, followTo: null } });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects a missing file", async () => {
  const fsImpl = memoryFs({});
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects a file not owned by the gateway service identity", async () => {
  const raw = JSON.stringify(goodProjection());
  const fsImpl = memoryFs({
    [FIXED]: makeEntry(raw, regularStat({ uid: 1000, size: Buffer.byteLength(raw) })),
  });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects a group/world-writable file", async () => {
  const raw = JSON.stringify(goodProjection());
  const fsImpl = memoryFs({
    [FIXED]: makeEntry(raw, regularStat({ mode: 0o666, size: Buffer.byteLength(raw) })),
  });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects an oversized file", async () => {
  const raw = JSON.stringify(goodProjection());
  const oversized = raw + " ".repeat(70 * 1024); // > 64 KiB
  const fsImpl = memoryFs({
    [FIXED]: makeEntry(oversized, regularStat({ size: Buffer.byteLength(oversized) })),
  });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects extra top-level keys", async () => {
  const raw = JSON.stringify(goodProjection({ extra: "nope" }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects extra app keys", async () => {
  const raw = JSON.stringify(
    goodProjection({ apps: [{ name: "openclaw", status: "running", note: "x" }] })
  );
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects an invalid app name (outside the ODS allowlist)", async () => {
  const raw = JSON.stringify(
    goodProjection({ apps: [{ name: "evil-container", status: "running" }] })
  );
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects an invalid app status", async () => {
  const raw = JSON.stringify(
    goodProjection({ apps: [{ name: "openclaw", status: "crashed" }] })
  );
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects duplicate app names", async () => {
  const raw = JSON.stringify(
    goodProjection({
      apps: [
        { name: "openclaw", status: "running" },
        { name: "openclaw", status: "healthy" },
      ],
    })
  );
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects an app count over the 64 maximum", async () => {
  const apps = [];
  let i = 0;
  while (i < 65) {
    apps.push({ name: "openclaw", status: "running" });
    i++;
  }
  const raw = JSON.stringify(goodProjection({ apps }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects a stale projection (older than 2 minutes)", async () => {
  const staleTs = new Date(Date.now() - 3 * 60 * 1000).toISOString();
  const raw = JSON.stringify(goodProjection({ timestamp: staleTs }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects a future timestamp", async () => {
  const futureTs = new Date(Date.now() + 5 * 60 * 1000).toISOString();
  const raw = JSON.stringify(goodProjection({ timestamp: futureTs }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects an unparseable timestamp", async () => {
  const raw = JSON.stringify(goodProjection({ timestamp: "not-a-date" }));
  const fsImpl = memoryFs({ [FIXED]: makeEntry(raw) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects a replacement race (dev/ino mismatch between lstat and open)", async () => {
  const raw = JSON.stringify(goodProjection());
  const stat = regularStat({ size: Buffer.byteLength(raw) });
  // The path lstat returns a different inode than the handle that is opened.
  const lstatStat = { ...stat, ino: 99 };
  const fsImpl = {
    lstat: async () => lstatStat,
    open: async () => makeEntry(raw, stat),
    readFile: async (fd) => fd.raw,
    stat: async (fd) => fd.stat,
    ownerUid: 0,
  };
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects malformed JSON", async () => {
  const fsImpl = memoryFs({ [FIXED]: makeEntry("{not json") });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects invalid UTF-8 instead of accepting replacement characters", async () => {
  const fsImpl = memoryFs({ [FIXED]: makeEntry(Buffer.from([0xc3, 0x28])) });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects a non-object root (array)", async () => {
  const fsImpl = memoryFs({ [FIXED]: makeEntry("[]") });
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.match(err.message, /unavailable/);
});

test("rejects wrong schema_version and wrong service", async () => {
  for (const bad of [
    goodProjection({ schema_version: 1 }),
    goodProjection({ service: "other" }),
  ]) {
    const fsImpl = memoryFs({ [FIXED]: makeEntry(JSON.stringify(bad)) });
    const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
    assert.ok(err);
    assert.match(err.message, /unavailable/);
  }
});

test("generic errors contain no path or raw content", async () => {
  // Missing file => generic rejection, never the path or raw content.
  const fsImpl = memoryFs({});
  const err = await asRejected(readProjection(FIXED, fsImpl, Date.now()));
  assert.ok(err);
  assert.ok(!err.message.includes(FIXED));
  assert.ok(!err.message.includes("/run"));
  assert.ok(!err.message.includes("schema_version"));
});

test("statusFileFromEnv uses the env override or the fixed default", () => {
  assert.equal(statusFileFromEnv({}), "/run/ods-pixel/ods-status.json");
  assert.equal(
    statusFileFromEnv({ PIXEL_ODS_STATUS_FILE: "/tmp/x.json" }),
    "/tmp/x.json"
  );
  assert.throws(
    () => statusFileFromEnv({ PIXEL_ODS_STATUS_FILE: "relative.json" }),
    /unavailable/
  );
});
