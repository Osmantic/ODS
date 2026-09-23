import { createHash } from "node:crypto";
import { lstat, readFile, readdir } from "node:fs/promises";
import { extname, join, relative } from "node:path";

const MAX_TEXT_BYTES = 2 * 1024 * 1024;
const TEXT_EXTENSIONS = new Set([".cjs", ".cts", ".d.ts", ".js", ".json", ".json5", ".jsx", ".md", ".mjs", ".mts", ".sh", ".ts", ".tsx", ".yaml", ".yml"]);
const CATEGORY_PATTERNS = {
  cli: /(^|\/)(bin|cli|commands?|args?|options?)(\/|\.|$)/i,
  config: /(config|schema|defaults?|validation)/i,
  gateway: /(gateway|server|auth|transport)/i,
  plugins: /(plugin|extension|tools?)/i,
  sessions: /(sessions?|subagents?|agents?)/i,
  sandbox: /(sandbox|docker|container|isolation)/i,
  state: /(state|migrat|credential|secret|oauth|token|recovery)/i,
};
const SIGNAL_PATTERNS = {
  authority: /\b(exec|spawn|shell|sudo|child_process|chmod|chown|unlink|delete|kill|writeFile|createWriteStream)\b/i,
  denial: /\b(deny|denied|forbid|forbidden|block|blocked|disabled|read.?only|loopback|workspace.?only|no.?new.?privileges|cap.?drop)\b/i,
  credential: /\b(credential|secret|token|oauth|password|api.?key|refresh.?token)\b/i,
  network: /\b(network|bind|listen|socket|proxy|port|fetch|https?|dns)\b/i,
  filesystem: /\b(filesystem|mount|path|home|workspace|directory|readFile|writeFile|chmod|chown)\b/i,
};
const SENSITIVE_INTERFACE = /(exec|shell|unsafe|sudo|root|auth|token|secret|credential|bind|listen|network|mount|path|write|delete)/i;
const MAX_PATH_SAMPLES = 20;

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function sortedUnique(values) {
  return [...new Set(values)].sort();
}

function flattenKeys(value, prefix = "", output = []) {
  if (!value || typeof value !== "object") return output;
  for (const key of Object.keys(value).sort()) {
    const path = prefix ? `${prefix}.${key}` : key;
    output.push(path);
    flattenKeys(value[key], path, output);
  }
  return output;
}

async function walk(root, directory = root, output = []) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Contract snapshot refused symlink: ${path}`);
    if (entry.isDirectory()) await walk(root, path, output);
    else if (entry.isFile() && entry.name !== ".pixel-extraction.json") output.push(path);
    else if (!entry.isFile()) throw new Error(`Contract snapshot refused non-regular file: ${path}`);
  }
  return output;
}

function categoriesFor(path) {
  const categories = Object.entries(CATEGORY_PATTERNS).filter(([, pattern]) => pattern.test(path)).map(([name]) => name);
  if (path === "package.json") categories.push("manifest");
  return sortedUnique(categories);
}

function interfaces(text) {
  const flags = [...text.matchAll(/(?:^|[^A-Za-z0-9])(--[a-z][a-z0-9-]{1,63})\b/g)].map((match) => match[1]);
  const environment = [...text.matchAll(/\b(OPENCLAW_[A-Z][A-Z0-9_]{2,80})\b/g)].map((match) => match[1]);
  const tools = [...text.matchAll(/\b((?:sessions|agents|gateway|sandbox|plugins)_[a-z][a-z0-9_]{2,80})\b/g)].map((match) => match[1]);
  const commands = [...text.matchAll(/\.command\(\s*["'`]([a-z][a-z0-9:_-]{1,80})/g)].map((match) => match[1]);
  return { flags: sortedUnique(flags), environment: sortedUnique(environment), tools: sortedUnique(tools), commands: sortedUnique(commands) };
}

function securitySignals(text, path, categories) {
  const output = [];
  const lines = text.split(/\r?\n/);
  for (const line of lines) {
    const normalized = line.trim().replace(/\s+/g, " ");
    if (!normalized) continue;
    for (const [kind, pattern] of Object.entries(SIGNAL_PATTERNS)) {
      if (!pattern.test(normalized)) continue;
      for (const category of categories.length ? categories : ["uncategorized"]) {
        output.push({ kind, category, path, lineSha256: digest(normalized) });
      }
    }
  }
  return output;
}

function normalizeManifest(value) {
  return {
    name: value.name ?? null,
    version: value.version ?? null,
    type: value.type ?? null,
    bin: value.bin ?? null,
    engines: value.engines ?? null,
    exportsKeys: flattenKeys(value.exports),
    scriptHashes: Object.fromEntries(Object.entries(value.scripts ?? {}).sort().map(([name, script]) => [name, digest(String(script))])),
    dependencies: Object.fromEntries(Object.entries(value.dependencies ?? {}).sort()),
    optionalDependencies: Object.fromEntries(Object.entries(value.optionalDependencies ?? {}).sort()),
    peerDependencies: Object.fromEntries(Object.entries(value.peerDependencies ?? {}).sort()),
  };
}

export async function snapshotPackage(root, identity) {
  const details = await lstat(root);
  if (!details.isDirectory() || details.isSymbolicLink()) throw new Error("Contract snapshot root must be a real directory");
  const extraction = JSON.parse(await readFile(join(root, ".pixel-extraction.json"), "utf8"));
  const files = await walk(root);
  const categoryFiles = Object.fromEntries([...Object.keys(CATEGORY_PATTERNS), "manifest"].map((name) => [name, []]));
  const collected = { flags: [], environment: [], tools: [], commands: [] };
  const signals = [];
  let manifest = null;
  for (const absolute of files.sort()) {
    const path = relative(root, absolute).replaceAll("\\", "/");
    const bytes = await readFile(absolute);
    const categories = categoriesFor(path);
    const record = { path, sha256: digest(bytes), size: bytes.length };
    for (const category of categories) categoryFiles[category].push(record);
    if (path === "package.json") manifest = normalizeManifest(JSON.parse(bytes.toString("utf8")));
    const compoundExtension = path.endsWith(".d.ts") ? ".d.ts" : extname(path).toLowerCase();
    if (bytes.length > MAX_TEXT_BYTES || !TEXT_EXTENSIONS.has(compoundExtension) || bytes.includes(0)) continue;
    const text = bytes.toString("utf8");
    const found = interfaces(text);
    for (const key of Object.keys(collected)) collected[key].push(...found[key]);
    signals.push(...securitySignals(text, path, categories));
  }
  if (!manifest) throw new Error("Contract snapshot found no package.json");
  return {
    schemaVersion: 1,
    identity,
    extraction: {
      archiveSha256: extraction.archiveSha256,
      treeSha256: extraction.treeSha256,
      fileCount: extraction.fileCount,
      totalBytes: extraction.totalBytes,
    },
    manifest,
    interfaces: Object.fromEntries(Object.entries(collected).map(([key, values]) => [key, sortedUnique(values)])),
    categories: Object.fromEntries(Object.entries(categoryFiles).map(([key, values]) => [key, values.sort((left, right) => left.path.localeCompare(right.path))])),
    signals: signals.sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))),
  };
}

function listDiff(before, after) {
  const left = new Set(before);
  const right = new Set(after);
  return { added: [...right].filter((item) => !left.has(item)).sort(), removed: [...left].filter((item) => !right.has(item)).sort() };
}

function fileDiff(before, after) {
  const left = new Map(before.map((item) => [item.path, item]));
  const right = new Map(after.map((item) => [item.path, item]));
  return {
    added: [...right.keys()].filter((path) => !left.has(path)).sort(),
    removed: [...left.keys()].filter((path) => !right.has(path)).sort(),
    changed: [...right.keys()].filter((path) => left.has(path) && left.get(path).sha256 !== right.get(path).sha256).sort(),
  };
}

function stableSignalPath(path) {
  if (!path.startsWith("dist/")) return path;
  return path.replace(/-([A-Za-z0-9_-]{8,12})(\.(?:d\.ts|[cm]?[jt]sx?))$/, (match, suffix, extension) => (
    /[A-Z0-9_]/.test(suffix) ? `-<content-chunk>${extension}` : match
  ));
}

function signalKey(value) {
  return `${value.kind}\0${value.category}\0${stableSignalPath(value.path)}\0${value.lineSha256}`;
}

function signalIdentity(value) {
  return `${value.kind}\0${value.category}\0${value.lineSha256}`;
}

function diffSignalMultisets(before, after) {
  const group = (values) => {
    const output = new Map();
    for (const value of values) {
      const key = signalIdentity(value);
      const records = output.get(key) ?? [];
      records.push(value);
      output.set(key, records);
    }
    for (const records of output.values()) records.sort((left, right) => signalKey(left).localeCompare(signalKey(right)));
    return output;
  };
  const left = group(before);
  const right = group(after);
  const added = [];
  const removed = [];
  for (const key of new Set([...left.keys(), ...right.keys()])) {
    const beforeRecords = left.get(key) ?? [];
    const afterRecords = right.get(key) ?? [];
    if (afterRecords.length > beforeRecords.length) added.push(...afterRecords.slice(beforeRecords.length));
    if (beforeRecords.length > afterRecords.length) removed.push(...beforeRecords.slice(afterRecords.length));
  }
  return { added, removed };
}

function summarizeSignals(signals) {
  const groups = new Map();
  for (const signal of signals) {
    const key = `${signal.kind}\0${signal.category}`;
    const current = groups.get(key) ?? { kind: signal.kind, category: signal.category, paths: new Set(), evidence: new Set() };
    current.paths.add(signal.path);
    current.evidence.add(signalKey(signal));
    groups.set(key, current);
  }
  return [...groups.values()].map((item) => {
    const paths = [...item.paths].sort();
    const evidence = [...item.evidence].sort();
    return {
      kind: item.kind,
      category: item.category,
      count: evidence.length,
      pathCount: paths.length,
      pathSamples: paths.slice(0, MAX_PATH_SAMPLES),
      evidenceSha256: digest(evidence.join("\n")),
    };
  }).sort((left, right) => `${left.kind}\0${left.category}`.localeCompare(`${right.kind}\0${right.category}`));
}

function aggregateBlockers(packageName, changes, rawSignals) {
  const groups = new Map();
  const add = (type, change, signal) => {
    const key = `${type}\0${change}\0${signal.kind}\0${signal.category}`;
    const current = groups.get(key) ?? { type, change, signal: signal.kind, category: signal.category, paths: new Set(), evidence: new Set() };
    current.paths.add(signal.path);
    current.evidence.add(signal.lineSha256 ? signalKey(signal) : JSON.stringify(signal));
    groups.set(key, current);
  };
  for (const signal of rawSignals.added) {
    if (signal.kind === "authority") add("new-authority", "added", signal);
    if (signal.kind === "credential") add("credential-path-change", "added", signal);
    if (["network", "filesystem"].includes(signal.kind) && ["gateway", "sandbox", "state", "config"].includes(signal.category)) add("surface-change", "added", signal);
  }
  for (const signal of rawSignals.removed) {
    if (signal.kind === "denial") add("removed-denial", "removed", signal);
    if (signal.kind === "credential") add("credential-path-change", "removed", signal);
    if (["network", "filesystem"].includes(signal.kind) && ["gateway", "sandbox", "state", "config"].includes(signal.category)) add("surface-change", "removed", signal);
  }
  for (const [kind, diff] of Object.entries(changes.interfaces)) {
    for (const value of diff.added) if (SENSITIVE_INTERFACE.test(value)) add("sensitive-interface", "added", { kind, category: "interface", path: value });
  }
  if (changes.manifest.changed.includes("scriptHashes")) add("package-script-change", "changed", { kind: "lifecycle", category: "manifest", path: "package.json" });
  return [...groups.values()].map((item) => {
    const paths = [...item.paths].sort();
    const evidence = [...item.evidence].sort();
    const finding = {
      package: packageName,
      type: item.type,
      change: item.change,
      signal: item.signal,
      category: item.category,
      count: evidence.length,
      pathCount: paths.length,
      pathSamples: paths.slice(0, MAX_PATH_SAMPLES),
      evidenceSha256: digest(evidence.join("\n")),
    };
    return { id: digest(JSON.stringify(finding)).slice(0, 16), ...finding };
  }).sort((left, right) => left.id.localeCompare(right.id));
}

export function diffSnapshots(before, after) {
  const categories = Object.fromEntries(Object.keys(before.categories).map((category) => [category, fileDiff(before.categories[category], after.categories[category] ?? [])]));
  const interfaceDiff = Object.fromEntries(Object.keys(before.interfaces).map((kind) => [kind, listDiff(before.interfaces[kind], after.interfaces[kind] ?? [])]));
  const rawSignals = diffSignalMultisets(before.signals, after.signals);
  const signals = {
    added: summarizeSignals(rawSignals.added),
    removed: summarizeSignals(rawSignals.removed),
    totals: { added: rawSignals.added.length, removed: rawSignals.removed.length },
  };
  const manifestChanged = Object.keys(before.manifest).filter((key) => JSON.stringify(before.manifest[key]) !== JSON.stringify(after.manifest[key])).sort();
  const changes = { manifest: { changed: manifestChanged }, interfaces: interfaceDiff, categories, signals };
  const blockers = aggregateBlockers(after.identity.name, changes, rawSignals);
  const reviewRequired = manifestChanged.length > 0
    || Object.values(interfaceDiff).some((item) => item.added.length || item.removed.length)
    || Object.values(categories).some((item) => item.added.length || item.removed.length || item.changed.length);
  return {
    package: after.identity.name,
    supported: { ...before.identity, extraction: before.extraction },
    candidate: { ...after.identity, extraction: after.extraction },
    changes,
    blockers,
    reviewRequired,
  };
}

export function buildContractDiff(packageDiffs, sourceCommit) {
  const blockers = packageDiffs.flatMap((item) => item.blockers);
  const reviewRequired = packageDiffs.some((item) => item.reviewRequired);
  return {
    schemaVersion: 1,
    sourceCommit,
    status: blockers.length ? "blocked" : reviewRequired ? "review-required" : "no-change",
    policy: {
      newAuthority: "blocking",
      removedDenial: "blocking",
      credentialPathChange: "blocking",
      networkOrFilesystemSurfaceChange: "blocking",
      capabilityRegression: "blocking during qualification",
    },
    packages: packageDiffs,
    blockers,
  };
}

export function renderContractDiff(report) {
  const rows = report.packages.map((item) => {
    const changed = Object.values(item.changes.categories).reduce((count, value) => count + value.added.length + value.removed.length + value.changed.length, 0);
    return `| ${item.package} | ${item.supported.version} | ${item.candidate.version} | ${changed} | ${item.blockers.length} | ${item.reviewRequired ? "yes" : "no"} |`;
  });
  const blockers = report.blockers.length
    ? report.blockers.map((item) => {
      const sample = item.pathSamples.length ? `; samples: ${item.pathSamples.map((path) => `\`${path}\``).join(", ")}` : "";
      return `- \`${item.id}\` ${item.package}: ${item.type} (${item.change}) in \`${item.category}\` — ${item.count} signal(s) across ${item.pathCount} path(s)${sample}`;
    }).join("\n")
    : "- None detected by the static blocking policy.";
  const accepted = Array.isArray(report.acceptedFindings) && report.acceptedFindings.length
    ? report.acceptedFindings.map((item) => `- \`${item.finding.id}\` — ${item.decision.reviewer}: ${item.decision.rationale}`).join("\n")
    : "- None.";
  return `# OpenClaw upstream contract diff\n\nStatus: **${report.status}**\n\n| Package | Supported | Candidate | Changed contract files | Blockers | Review required |\n|---|---:|---:|---:|---:|---|\n${rows.join("\n")}\n\n## Blocking findings\n\n${blockers}\n\n## Reviewed compatible findings\n\n${accepted}\n\nThe machine-readable report contains exact file hashes, normalized interfaces, signal hashes, and any exact review decisions. Source lines are intentionally not copied into evidence. Every blocker requires an explicit reviewed disposition before qualification can pass; a review cannot waive a security finding.\n`;
}
