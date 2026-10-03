import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, rename, rm } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { GoalServiceLifecycleCliError, runGoalServiceLifecycleCommand } from "./goal-service-lifecycle-cli.mjs";
import { GoalServiceLifecycleError } from "./goal-service-lifecycle.mjs";
import { renderGoalServiceUnits } from "./goal-service-units.mjs";
import { goalSha256 } from "./goals.mjs";

const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const authority = Object.freeze({ grantsExecution: false, grantsInstall: false, grantsEnable: false, grantsLease: false, grantsExternalEffects: false });
const boundary = "Private atomic systemd render bundle only. Rendering grants no work execution, installation, service enablement, lease, external effect, or completion authority.";

export class GoalServiceCliError extends Error {}

function fail(message) { throw new GoalServiceCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv[0] !== "render" || (argv.length - 1) % 2 !== 0) fail("Usage: goal-service-cli.mjs render --config FILE --output DIR --install-root DIR [--confirm-config-sha256 HASH --confirm-goal-sha256 HASH --node PATH --flock PATH --user NAME --group NAME --docker-group NAME --interval SECONDS]");
  const allowed = new Set(["--config", "--output", "--install-root", "--confirm-config-sha256", "--confirm-goal-sha256", "--node", "--flock", "--user", "--group", "--docker-group", "--interval"]);
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("goal service render arguments are invalid, unknown, or duplicated");
    values[key] = value;
  }
  for (const key of ["--config", "--output", "--install-root"]) if (!values[key]) fail(`goal service render is missing ${key}`);
  const interval = values["--interval"] === undefined ? 30 : Number(values["--interval"]);
  if (!Number.isSafeInteger(interval)) fail("goal service render interval is invalid");
  for (const key of ["--confirm-config-sha256", "--confirm-goal-sha256"]) {
    if (values[key] !== undefined && !/^[a-f0-9]{64}$/u.test(values[key])) fail("goal service render confirmation is invalid");
  }
  return {
    configPath: resolve(values["--config"]), outputPath: resolve(values["--output"]),
    installRoot: values["--install-root"], nodePath: values["--node"] ?? "/usr/bin/node",
    flockPath: values["--flock"] ?? "/usr/bin/flock", serviceUser: values["--user"] ?? "pixel-work",
    serviceGroup: values["--group"] ?? "pixel-work", dockerGroup: values["--docker-group"] ?? "docker",
    intervalSeconds: interval, confirmConfigSha256: values["--confirm-config-sha256"],
    confirmGoalSha256: values["--confirm-goal-sha256"],
  };
}

async function privateDirectory(path, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-private`);
  return path;
}

async function writePrivate(path, text) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(text, "utf8"); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

export async function renderGoalServiceBundle(argv) {
  const options = parseArguments(argv);
  const outputName = basename(options.outputPath);
  if (!OUTPUT_RE.test(outputName) || join(dirname(options.outputPath), outputName) !== options.outputPath) fail("goal service output directory name is invalid");
  const parent = await privateDirectory(dirname(options.outputPath), "goal service output parent");
  if (await lstat(options.outputPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("goal service output already exists");
  const { config, goal } = await loadGoalCycleConfiguration(options.configPath);
  const configSha256 = sha(config), immutableGoalSha256 = goalSha256(goal);
  if (options.confirmConfigSha256 !== undefined && options.confirmConfigSha256 !== configSha256) fail("goal service render configuration differs from its exact confirmation");
  if (options.confirmGoalSha256 !== undefined && options.confirmGoalSha256 !== immutableGoalSha256) fail("goal service render goal differs from its exact confirmation");
  const units = renderGoalServiceUnits({ configPath: options.configPath, config, goal, ...options });
  const manifest = {
    schemaVersion: 1, operation: "pixel-work-goal-service-render", goalId: goal.goalId,
    goalSha256: immutableGoalSha256, configSha256, serviceName: units.serviceName,
    serviceSha256: sha(units.service), timerName: units.timerName, timerSha256: sha(units.timer),
    pathName: units.pathName, pathSha256: sha(units.path),
    authority: { ...authority }, boundary,
  };
  const manifestText = `${JSON.stringify(manifest, null, 2)}\n`;
  const staging = join(parent, `.pixel-work-service-${randomBytes(8).toString("hex")}`);
  try {
    await mkdir(staging, { mode: 0o700 });
    await writePrivate(join(staging, units.serviceName), units.service);
    await writePrivate(join(staging, units.timerName), units.timer);
    await writePrivate(join(staging, units.pathName), units.path);
    await writePrivate(join(staging, "service-bundle.json"), manifestText);
    await syncDirectory(staging);
    await rename(staging, options.outputPath);
    await syncDirectory(parent);
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
  return Object.freeze({
    schemaVersion: 1, operation: manifest.operation, goalId: manifest.goalId,
    serviceName: manifest.serviceName, serviceSha256: manifest.serviceSha256,
    timerName: manifest.timerName, timerSha256: manifest.timerSha256,
    pathName: manifest.pathName, pathSha256: manifest.pathSha256,
    manifestSha256: sha(manifestText), outputName, authority: { ...authority }, boundary,
  });
}

export async function main(argv = process.argv.slice(2)) {
  const receipt = argv[0] === "render" ? await renderGoalServiceBundle(argv) : await runGoalServiceLifecycleCommand(argv);
  process.stdout.write(`${JSON.stringify(receipt)}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    const expected = error instanceof GoalServiceCliError || error instanceof GoalServiceLifecycleCliError || error instanceof GoalServiceLifecycleError;
    process.stderr.write(`pixel-work-service: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const goalServiceRenderBoundary = boundary;
