import { lstat } from "node:fs/promises";
import { isAbsolute, parse, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { createWorkProviderEgressProxy, parseBoundWorkProviderPrivatePolicy } from "./egress-proxy.mjs";

export class WorkProviderDevProxyCliError extends Error {}
function fail(message) { throw new WorkProviderDevProxyCliError(message); }

function argumentsFor(argv) {
  if (!Array.isArray(argv) || argv.length !== 4 || argv[0] !== "--policy" || argv[2] !== "--sha256") fail("usage: dev-proxy-cli.mjs --policy ABSOLUTE_PATH --sha256 HEX");
  const path = argv[1], sha256 = argv[3];
  if (typeof path !== "string" || !isAbsolute(path) || resolve(path) === parse(resolve(path)).root || !/^[a-f0-9]{64}$/u.test(sha256 ?? "")) fail("development proxy arguments are invalid");
  return { path: resolve(path), sha256 };
}

export async function readOwnerPrivateWorkProviderPolicy(path) {
  let observed;
  try { observed = await readBoundedRegularFile(path, 256 * 1024, "development proxy policy"); }
  catch { fail("development proxy policy must be one bounded real file"); }
  const pathInfo = await lstat(path).catch(() => null);
  if (observed.bytes.length < 2 || observed.details.nlink !== 1 || !pathInfo?.isFile() || pathInfo.isSymbolicLink()
    || pathInfo.dev !== observed.details.dev || pathInfo.ino !== observed.details.ino) {
    observed.bytes.fill(0);
    fail("development proxy policy must be one bounded real file");
  }
  if (process.platform !== "win32" && (observed.details.uid !== process.geteuid() || (observed.details.mode & 0o077) !== 0)) fail("development proxy policy must be owner-only");
  return observed.bytes;
}

export async function runWorkProviderDevProxy(argv = process.argv.slice(2)) {
  const options = argumentsFor(argv), parsed = parseBoundWorkProviderPrivatePolicy(await readOwnerPrivateWorkProviderPolicy(options.path), options.sha256);
  const server = createWorkProviderEgressProxy(parsed);
  server.on("error", () => { process.stderr.write("Pixel development provider proxy failed closed.\n"); process.exitCode = 70; });
  server.listen(3128, "127.0.0.1");
  return server;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runWorkProviderDevProxy().catch(() => { process.stderr.write("Pixel development provider proxy failed closed.\n"); process.exitCode = 70; });
}
