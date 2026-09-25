#!/usr/bin/env node
/** Run one host-loopback model proxy with a caller-selected private receipt location. */

import { lstat } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createModelProxyFromConfigFile, WorkModelProxyError, writeModelProxyReport,
} from "../work-model-proxy/proxy.mjs";

function fail(message) { throw new WorkModelProxyError("invalid-runtime", message); }

function argumentsFrom(argv) {
  if (!Array.isArray(argv) || argv.length !== 4 || argv[0] !== "--config" || argv[2] !== "--receipt") fail("usage");
  const configPath = resolve(argv[1]), receiptPath = resolve(argv[3]);
  if (!isAbsolute(argv[1]) || !isAbsolute(argv[3]) || configPath !== argv[1] || receiptPath !== argv[3]) fail("paths must be exact absolute paths");
  if (configPath === receiptPath) fail("config and receipt paths must be distinct");
  return { configPath, receiptPath };
}

export async function runAssistantModelProxy(argv = process.argv.slice(2), dependencies = {}) {
  const { configPath, receiptPath } = argumentsFrom(argv);
  const receiptParent = await lstat(dirname(receiptPath)).catch(() => null);
  const existing = await lstat(receiptPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!receiptParent?.isDirectory() || receiptParent.isSymbolicLink() || existing !== null) fail("receipt destination must be a new file in a real private directory");
  if (process.platform !== "win32" && (receiptParent.uid !== process.geteuid() || (receiptParent.mode & 0o077) !== 0)) fail("receipt directory must be owner-only");
  const create = dependencies.createModelProxyFromConfigFile ?? createModelProxyFromConfigFile;
  const writer = dependencies.writeModelProxyReport ?? writeModelProxyReport;
  const proxy = await create(configPath, { reportWriter: async (_configuredPath, report) => writer(receiptPath, report) });
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    await proxy.close();
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  await proxy.listen();
  return proxy;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  runAssistantModelProxy().catch((error) => {
    process.stderr.write(`pixel-assistant-model-proxy: ${error instanceof WorkModelProxyError ? error.code : "startup-failed"}\n`);
    process.exitCode = 1;
  });
}
