import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  capabilityPackSshKeygenPath,
  CapabilityPackInstallationError, inspectCapabilityPack, installCapabilityPack,
  recoverCapabilityPackRemoval, removeCapabilityPack, reviewCapabilityPackRemoval,
  signCapabilityPack, statusCapabilityPacks, verifyCapabilityPack,
} from "./capability-pack-installation.mjs";
import {
  admitCapabilityImage, CapabilityImageAdmissionError, recoverCapabilityImageCleanup,
  recoverCapabilityImageRevocation, reviewCapabilityImageRevocation,
  revokeCapabilityImageAdmission, statusCapabilityImages,
} from "./capability-image-admission.mjs";
import {
  CapabilityHealthError, probeCapabilityHealth, recoverCapabilityHealth, statusCapabilityHealth,
} from "./capability-health.mjs";

function fail(message) { throw new CapabilityPackInstallationError(message); }

function parse(argv) {
  const [command, ...rest] = argv;
  const commands = ["inspect", "sign", "verify", "install", "status", "remove-review", "remove", "recover", "image-admit", "image-status", "image-revoke-review", "image-revoke", "image-recover", "image-cleanup-recover", "health-probe", "health-status", "health-recover"];
  if (!new Set(commands).has(command)) fail(`Usage: capability-pack-cli.mjs ${commands.join("|")} [options]`);
  const flags = new Set(["--confirm"]), values = { command };
  for (let index = 0; index < rest.length; index += 1) {
    const key = rest[index];
    if (flags.has(key)) {
      if (Object.hasOwn(values, key)) fail("capability pack options are duplicated");
      values[key] = true;
      continue;
    }
    const value = rest[index + 1];
    if (!new Set(["--pack", "--signature", "--signing-key", "--signature-output", "--allowed-signers", "--identity", "--state-root", "--pack-id", "--version", "--pack-sha256", "--confirm-review-sha256", "--docker-config"]).has(key) || !value || value.startsWith("--") || Object.hasOwn(values, key)) fail("capability pack options are invalid, unknown, or duplicated");
    values[key] = value; index += 1;
  }
  const allowed = {
    inspect: ["--pack"], sign: ["--pack", "--signing-key", "--signature-output", "--identity", "--confirm"],
    verify: ["--pack", "--signature", "--allowed-signers", "--identity"],
    install: ["--pack", "--signature", "--allowed-signers", "--identity", "--state-root", "--confirm"],
    status: ["--allowed-signers", "--state-root"],
    "remove-review": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    remove: ["--pack-id", "--version", "--confirm-review-sha256", "--allowed-signers", "--state-root"],
    recover: ["--pack-id", "--version", "--pack-sha256", "--confirm-review-sha256", "--allowed-signers", "--state-root"],
    "image-admit": ["--pack-id", "--version", "--allowed-signers", "--state-root", "--docker-config", "--confirm"],
    "image-status": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    "image-revoke-review": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    "image-revoke": ["--pack-id", "--version", "--confirm-review-sha256", "--allowed-signers", "--state-root"],
    "image-recover": ["--pack-id", "--version", "--pack-sha256", "--confirm-review-sha256", "--state-root"],
    "image-cleanup-recover": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    "health-probe": ["--pack-id", "--version", "--allowed-signers", "--state-root", "--docker-config", "--confirm"],
    "health-status": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    "health-recover": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
  }[command];
  if (Object.keys(values).some((key) => key !== "command" && !allowed.includes(key))) fail("capability pack option is not valid for this command");
  const required = {
    inspect: ["--pack"], sign: ["--pack", "--signing-key", "--signature-output", "--identity", "--confirm"],
    verify: ["--pack", "--signature", "--allowed-signers", "--identity"],
    install: ["--pack", "--signature", "--allowed-signers", "--identity", "--state-root", "--confirm"],
    status: ["--allowed-signers", "--state-root"],
    "remove-review": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    remove: ["--pack-id", "--version", "--confirm-review-sha256", "--allowed-signers", "--state-root"],
    recover: ["--pack-id", "--version", "--pack-sha256", "--confirm-review-sha256", "--allowed-signers", "--state-root"],
    "image-admit": ["--pack-id", "--version", "--allowed-signers", "--state-root", "--docker-config", "--confirm"],
    "image-status": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    "image-revoke-review": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    "image-revoke": ["--pack-id", "--version", "--confirm-review-sha256", "--allowed-signers", "--state-root"],
    "image-recover": ["--pack-id", "--version", "--pack-sha256", "--confirm-review-sha256", "--state-root"],
    "image-cleanup-recover": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    "health-probe": ["--pack-id", "--version", "--allowed-signers", "--state-root", "--docker-config", "--confirm"],
    "health-status": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
    "health-recover": ["--pack-id", "--version", "--allowed-signers", "--state-root"],
  }[command];
  if (required.some((key) => !values[key])) fail(`capability pack ${command} is missing a required option or confirmation`);
  return values;
}

function absolute(value, label) {
  const path = resolve(value);
  if (path !== value) fail(`${label} must be absolute`);
  return path;
}

export async function main(argv = process.argv.slice(2)) {
  const values = parse(argv), sshKeygenPath = capabilityPackSshKeygenPath;
  let receipt;
  if (values.command === "inspect") receipt = await inspectCapabilityPack(absolute(values["--pack"], "capability pack path"));
  else if (values.command === "sign") receipt = await signCapabilityPack({ packPath: absolute(values["--pack"], "capability pack path"), signingKeyPath: absolute(values["--signing-key"], "signing key path"), signatureOutputPath: absolute(values["--signature-output"], "signature output path"), identity: values["--identity"], sshKeygenPath });
  else if (values.command === "verify") receipt = await verifyCapabilityPack({ packPath: absolute(values["--pack"], "capability pack path"), signaturePath: absolute(values["--signature"], "signature path"), allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), identity: values["--identity"], sshKeygenPath });
  else if (values.command === "install") receipt = await installCapabilityPack({ stateRoot: absolute(values["--state-root"], "state root"), packPath: absolute(values["--pack"], "capability pack path"), signaturePath: absolute(values["--signature"], "signature path"), allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), identity: values["--identity"], sshKeygenPath });
  else if (values.command === "status") receipt = await statusCapabilityPacks({ stateRoot: absolute(values["--state-root"], "state root"), allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else if (values.command === "remove-review") receipt = await reviewCapabilityPackRemoval({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else if (values.command === "remove") receipt = await removeCapabilityPack({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], confirmReviewSha256: values["--confirm-review-sha256"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else if (values.command === "recover") receipt = await recoverCapabilityPackRemoval({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], packSha256: values["--pack-sha256"], confirmReviewSha256: values["--confirm-review-sha256"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else if (values.command === "image-admit") receipt = await admitCapabilityImage({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), dockerConfigPath: absolute(values["--docker-config"], "Docker configuration path"), sshKeygenPath });
  else if (values.command === "image-status") receipt = await statusCapabilityImages({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else if (values.command === "image-revoke-review") receipt = await reviewCapabilityImageRevocation({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else if (values.command === "image-revoke") receipt = await revokeCapabilityImageAdmission({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], confirmReviewSha256: values["--confirm-review-sha256"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else if (values.command === "image-recover") receipt = await recoverCapabilityImageRevocation({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], packSha256: values["--pack-sha256"], confirmReviewSha256: values["--confirm-review-sha256"] });
  else if (values.command === "image-cleanup-recover") receipt = await recoverCapabilityImageCleanup({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else if (values.command === "health-probe") receipt = await probeCapabilityHealth({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), dockerConfigPath: absolute(values["--docker-config"], "Docker configuration path"), sshKeygenPath });
  else if (values.command === "health-status") receipt = await statusCapabilityHealth({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  else receipt = await recoverCapabilityHealth({ stateRoot: absolute(values["--state-root"], "state root"), id: values["--pack-id"], version: values["--version"], allowedSignersPath: absolute(values["--allowed-signers"], "allowed signers path"), sshKeygenPath });
  process.stdout.write(`${JSON.stringify(receipt)}\n`);
  if (values.command === "health-probe" && receipt.status !== "passing-disabled") process.exitCode = 2;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-capability-pack: ${error instanceof CapabilityPackInstallationError || error instanceof CapabilityImageAdmissionError || error instanceof CapabilityHealthError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
