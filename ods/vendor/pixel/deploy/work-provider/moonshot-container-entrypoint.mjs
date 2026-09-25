import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { runMoonshotSmoke } from "./moonshot-smoke-cli.mjs";

const SHA = /^[a-f0-9]{64}$/u;
const SECRET_ENVIRONMENT = /(?:API[_-]?KEY|AUTH|CREDENTIAL|PASSWORD|PRIVATE[_-]?KEY|SECRET|SESSION|TOKEN)/iu;
const CREDENTIAL_FILES = new Set(["moonshot-kimi-key", "provider-key"]);

export class MoonshotContainerEntrypointError extends Error {}
function fail(message) { throw new MoonshotContainerEntrypointError(message); }

export async function runMoonshotContainerEntrypoint({ argv = process.argv.slice(2), environment = process.env, run = runMoonshotSmoke } = {}) {
  if (!Array.isArray(argv) || argv.length !== 0) fail("Moonshot container entrypoint accepts no arguments");
  const policySha256 = environment.PIXEL_WORK_PROVIDER_POLICY_SHA256;
  if (!SHA.test(policySha256 ?? "")) fail("Moonshot container policy binding is invalid");
  const credentialFile = environment.PIXEL_WORK_PROVIDER_CREDENTIAL_FILE;
  if (!CREDENTIAL_FILES.has(credentialFile)) fail("Moonshot container credential filename is invalid");
  for (const [name, value] of Object.entries(environment)) {
    if (!["PIXEL_WORK_PROVIDER_POLICY_SHA256", "PIXEL_WORK_PROVIDER_CREDENTIAL_FILE"].includes(name)
      && SECRET_ENVIRONMENT.test(name) && value) fail("Moonshot container received forbidden ambient credential material");
  }
  process.umask(0o077);
  return run([
    "--policy", "/run/pixel-policy/policy.json", "--sha256", policySha256,
    "--credential", `/run/pixel-credential/${credentialFile}`, "--ledger-root", "/state/ledger",
  ], { proxyHost: "pixel-provider-egress" });
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runMoonshotContainerEntrypoint().then((receipt) => {
    process.stdout.write(`${JSON.stringify(receipt)}\n`);
    if (receipt.status !== "passed") process.exitCode = 1;
  }).catch(() => {
    process.stderr.write("Pixel isolated Moonshot smoke failed closed. Inspect the content-free run ledger before any retry.\n");
    process.exitCode = 70;
  });
}
