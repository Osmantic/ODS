import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, realpath, rm, stat } from "node:fs/promises";
import { isAbsolute, join, parse, resolve } from "node:path";

import {
  canonical, validateWorkCodexEgressPolicy, validateWorkCodexPolicy,
} from "../../scripts/lib/work-contract.mjs";
import {
  readWorkCodexApiCredential, readWorkCodexChatgptAuthCache, replaceWorkCodexChatgptAuthCache,
} from "./credential-custody.mjs";
import { encodeWorkCodexContainerFrame } from "./container-protocol.mjs";
import { parseCredentialFreeCodexCliTranscript, validateWorkCodexCliPayload } from "./codex-cli-qualification.mjs";

const HEX_12 = /^[a-f0-9]{12}$/u;
const HEX_64 = /^[a-f0-9]{64}$/u;
const NAME = /^[a-z][a-z0-9_.-]{1,62}$/u;
const MAX_INSPECTION_BYTES = 2 * 1024 * 1024;
const MAX_STDERR_BYTES = 64 * 1024;

export class IsolatedWorkCodexCliError extends Error {}
function fail(message) { throw new IsolatedWorkCodexCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} failed validation: ${errors.join("; ")}`); }
function exactKeys(value, keys, label) { if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} has unsupported fields`); }
function hasAll(value) { return Array.isArray(value) && value.some((item) => String(item).toUpperCase() === "ALL"); }
function noNewPrivileges(value) { return Array.isArray(value) && value.some((item) => String(item).toLowerCase().replaceAll("=", ":") === "no-new-privileges:true"); }
function privateIp(value) { const parts = String(value).split(".").map(Number); return parts.length === 4 && parts.every((part) => Number.isInteger(part) && part >= 0 && part <= 255) && (parts[0] === 10 || parts[0] === 192 && parts[1] === 168 || parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31); }

async function regularExecutable(path, label) {
  if (typeof path !== "string" || !isAbsolute(path)) fail(`${label} must be an absolute path`);
  const result = resolve(path), info = await lstat(result).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || process.platform !== "win32" && (info.mode & 0o111) === 0) fail(`${label} must be an executable single-link regular file`);
  return result;
}

async function privateJson(path, label, maximumBytes = 128 * 1024) {
  if (typeof path !== "string" || !isAbsolute(path)) fail(`${label} path must be absolute`);
  const target = resolve(path), info = await lstat(target).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size < 2 || info.size > maximumBytes || await realpath(target).catch(() => null) !== target) fail(`${label} must be a bounded single-link regular file`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-only`);
  const handle = await open(target, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail(`${label} could not be opened safely`));
  let bytes;
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== info.dev || opened.ino !== info.ino || opened.size !== info.size) fail(`${label} changed during validation`);
    bytes = await handle.readFile();
  } finally { await handle.close(); }
  if (bytes.includes(0)) fail(`${label} contains invalid bytes`);
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail(`${label} is not strict UTF-8`); }
  let value; try { value = JSON.parse(text); } catch { fail(`${label} is not valid JSON`); }
  if (text.trim() !== canonical(value)) fail(`${label} must be canonical JSON`);
  return value;
}

function dockerEnvironment(paths) {
  const environment = { HOME: paths.dockerHome, USERPROFILE: paths.dockerHome, DOCKER_CONFIG: paths.dockerConfig, TMPDIR: paths.tmp, TMP: paths.tmp, TEMP: paths.tmp, LANG: "C.UTF-8", LC_ALL: "C.UTF-8", NO_COLOR: "1", PATH: "" };
  if (process.platform === "win32") { if (process.env.SystemRoot) environment.SystemRoot = process.env.SystemRoot; if (process.env.WINDIR) environment.WINDIR = process.env.WINDIR; }
  return Object.freeze(environment);
}

function stopProcessTree(child) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "win32") {
    const root = process.env.SystemRoot ?? "C:\\Windows", killer = spawn(join(root, "System32", "taskkill.exe"), ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore", windowsHide: true, shell: false });
    killer.on("error", () => { try { child.kill(); } catch { /* already gone */ } }); return;
  }
  try { process.kill(-child.pid, "SIGKILL"); } catch { try { child.kill("SIGKILL"); } catch { /* already gone */ } }
}

async function runCaptured({ command, args, environment, stdin = Buffer.alloc(0), timeoutMilliseconds = 5000, stdoutLimit = MAX_INSPECTION_BYTES, signal = null, outputPath = null, outputLimit = null }) {
  return new Promise((resolvePromise, rejectPromise) => {
    let child;
    try { child = spawn(command, args, { env: environment, stdio: ["pipe", "pipe", "pipe"], shell: false, windowsHide: true, detached: process.platform !== "win32" }); }
    catch { rejectPromise(new IsolatedWorkCodexCliError("isolated Codex Docker command could not start")); return; }
    const stdout = []; let stdoutBytes = 0, stderrBytes = 0, failure = null, settled = false;
    const reject = (message) => { if (!failure) failure = message; stopProcessTree(child); };
    const onAbort = () => reject("isolated Codex Docker command was aborted");
    if (signal?.aborted) onAbort(); else signal?.addEventListener("abort", onAbort, { once: true });
    child.stdout.on("data", (chunk) => { stdoutBytes += chunk.length; if (stdoutBytes > stdoutLimit) reject("isolated Codex Docker output exceeded its byte ceiling"); else stdout.push(chunk); });
    child.stderr.on("data", (chunk) => { stderrBytes += chunk.length; if (stderrBytes > MAX_STDERR_BYTES) reject("isolated Codex Docker diagnostics exceeded their byte ceiling"); });
    child.stdin.on("error", () => reject("isolated Codex Docker input stream failed")); child.on("error", () => reject("isolated Codex Docker command could not start"));
    const timer = setTimeout(() => reject("isolated Codex Docker command timed out"), timeoutMilliseconds);
    const monitor = outputPath && outputLimit ? setInterval(() => { stat(outputPath).then((info) => { if (!info.isFile() || info.size > outputLimit) reject("isolated Codex final output exceeded its live boundary"); }).catch((error) => { if (error.code !== "ENOENT") reject("isolated Codex final output became unsafe"); }); }, 20) : null;
    child.on("close", (code, closeSignal) => {
      if (settled) return; settled = true; clearTimeout(timer); if (monitor) clearInterval(monitor); signal?.removeEventListener("abort", onAbort);
      if (failure) rejectPromise(new IsolatedWorkCodexCliError(failure));
      else if (code !== 0 || closeSignal !== null) rejectPromise(new IsolatedWorkCodexCliError("isolated Codex Docker command exited unsuccessfully"));
      else resolvePromise(Buffer.concat(stdout));
    });
    child.stdin.end(stdin);
  });
}

function parseDockerJson(bytes, label) {
  if (!Buffer.isBuffer(bytes) || bytes.includes(0)) fail(`${label} contains invalid bytes`);
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail(`${label} is not strict UTF-8`); }
  let value; try { value = JSON.parse(text); } catch { fail(`${label} is not valid JSON`); }
  if (!Array.isArray(value) || value.length !== 1 || !value[0] || typeof value[0] !== "object" || Array.isArray(value[0])) fail(`${label} must contain exactly one object`);
  return value[0];
}

export function validateIsolatedCodexImageInspection(image, policy) {
  schema("Codex work policy", validateWorkCodexPolicy(policy));
  const labels = image?.Config?.Labels ?? {};
  if (image?.Id !== policy.transport.runnerImageId || !Array.isArray(image?.RepoDigests) || !image.RepoDigests.includes(policy.transport.runnerImageRef) || image?.Os !== "linux" || image?.Architecture !== "amd64" || labels["org.osmantic.pixel.codex-version"] !== policy.transport.codexVersion || labels["org.osmantic.pixel.codex-binary-sha256"] !== policy.transport.codexBinarySha256 || labels["org.osmantic.pixel.entrypoint-sha256"] !== policy.transport.entrypointSha256) fail("Codex runner image differs from its manifest, local-image, binary, or entrypoint pins");
  return true;
}

export function validateIsolatedCodexNetworkInspection(network, runtime) {
  const containers = network?.Containers ?? {}, entries = Object.entries(containers);
  if (network?.Id !== runtime.networkId || network?.Name !== runtime.networkName || network?.Driver !== "bridge" || network?.Internal !== true || network?.Attachable !== false || entries.length !== 1) fail("Codex worker network is not one exact internal bridge");
  const [id, proxy] = entries[0] ?? [];
  if (id !== runtime.proxyContainerId || proxy?.Name !== runtime.proxyName || String(proxy?.IPv4Address ?? "").split("/")[0] !== runtime.proxyIp || !privateIp(runtime.proxyIp)) fail("Codex worker network proxy attachment is invalid");
  return true;
}

export function validateIsolatedCodexProxyInspection(proxy, policy, runtime) {
  const host = proxy?.HostConfig ?? {}, networks = proxy?.NetworkSettings?.Networks ?? {}, networkNames = Object.keys(networks).sort(), expectedNetworks = [runtime.externalNetworkName, runtime.networkName].sort();
  const environment = proxy?.Config?.Env ?? [], labels = proxy?.Config?.Labels ?? {}, internal = networks[runtime.networkName], mounts = proxy?.Mounts ?? [], sensitiveEnvironment = environment.some((entry) => /(?:credential|secret|token|password|api[_-]?key|private[_-]?key)=/iu.test(String(entry)));
  const policyEnvironment = environment.filter((entry) => String(entry).startsWith("PIXEL_CODEX_EGRESS_POLICY_SHA256="));
  if (proxy?.Id !== runtime.proxyContainerId || proxy?.Name !== `/${runtime.proxyName}` || proxy?.Image !== policy.transport.proxyImageDigest || proxy?.State?.Running !== true || proxy?.State?.Paused === true || proxy?.State?.Restarting === true) fail("Codex egress proxy identity or state is invalid");
  if (host.Privileged !== false || host.ReadonlyRootfs !== true || !hasAll(host.CapDrop) || !noNewPrivileges(host.SecurityOpt) || host.NetworkMode === "host" || !Number.isSafeInteger(host.PidsLimit) || host.PidsLimit < 1 || host.PidsLimit > 128 || !Number.isSafeInteger(host.Memory) || host.Memory < 128 * 1024 * 1024 || host.MemorySwap !== host.Memory || (host.Devices ?? []).length !== 0 || Object.keys(host.PortBindings ?? {}).length !== 0) fail("Codex egress proxy host boundary is invalid");
  if (String(proxy?.Config?.User ?? "") === "" || /^(?:0|root)(?::|$)/u.test(String(proxy.Config.User)) || sensitiveEnvironment) fail("Codex egress proxy identity or environment is invalid");
  if (policyEnvironment.length !== 1 || policyEnvironment[0] !== `PIXEL_CODEX_EGRESS_POLICY_SHA256=${policy.transport.proxyPolicySha256}` || labels["org.osmantic.pixel.egress-policy-sha256"] !== policy.transport.proxyPolicySha256) fail("Codex egress proxy did not bind the exact loaded policy");
  if (canonical(networkNames) !== canonical(expectedNetworks) || internal?.IPAddress !== runtime.proxyIp || !Array.isArray(internal?.Aliases) || !internal.Aliases.includes("pixel-codex-egress")) fail("Codex egress proxy network attachments are invalid");
  if (mounts.length !== 1 || mounts[0]?.Type !== "bind" || resolve(mounts[0]?.Source ?? "") !== resolve(runtime.proxyPolicyPath) || mounts[0]?.Destination !== "/etc/pixel-egress/policy.json" || mounts[0]?.RW !== false) fail("Codex egress proxy policy mount is invalid");
  return true;
}

function validateRuntime(runtime) {
  exactKeys(runtime, ["networkName", "networkId", "proxyName", "proxyContainerId", "proxyIp", "externalNetworkName", "proxyPolicyPath", "brokerUid", "brokerGid"], "isolated Codex runtime");
  if (![runtime.networkName, runtime.proxyName, runtime.externalNetworkName].every((value) => NAME.test(value ?? "")) || !HEX_64.test(runtime.networkId ?? "") || !HEX_64.test(runtime.proxyContainerId ?? "") || !privateIp(runtime.proxyIp) || typeof runtime.proxyPolicyPath !== "string" || !isAbsolute(runtime.proxyPolicyPath) || !Number.isSafeInteger(runtime.brokerUid) || runtime.brokerUid < 1 || runtime.brokerUid > 65535 || !Number.isSafeInteger(runtime.brokerGid) || runtime.brokerGid < 1 || runtime.brokerGid > 65535) fail("isolated Codex runtime is invalid");
}

export function buildIsolatedCodexDockerCommand({ dockerPath, bootstrapArguments = [], policy, runtime, paths, runtimeId }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy)); validateRuntime(runtime);
  if (typeof dockerPath !== "string" || !isAbsolute(dockerPath) || !Array.isArray(bootstrapArguments) || bootstrapArguments.length > 1 || bootstrapArguments.some((value) => typeof value !== "string" || !isAbsolute(value)) || !HEX_12.test(runtimeId ?? "")) fail("isolated Codex Docker command inputs are invalid");
  const containerName = `pixel-codex-${runtimeId}`, proxyUrl = `http://${runtime.proxyIp}:${policy.transport.proxyPort}`;
  return Object.freeze({
    executable: dockerPath,
    containerName,
    args: Object.freeze([
      ...bootstrapArguments, "run", "--rm", "--cidfile", paths.cidFile, "--name", containerName, "--pull", "never", "--read-only",
      "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--privileged=false", "--network", runtime.networkName,
      "--dns", "127.0.0.1", "--dns-option", "attempts:1", "--dns-option", "timeout:1",
      "--ipc", "none", "--cgroupns", "private", "--pids-limit", String(policy.transport.maxPids),
      "--memory", `${policy.transport.maxMemoryMiB}m`, "--memory-swap", `${policy.transport.maxMemoryMiB}m`, "--cpus", String(policy.transport.maxCpuCores),
      "--ulimit", "nofile=256:256", "--ulimit", "nproc=64:64", "--ulimit", `fsize=${policy.provider.maxOutputBytes}:${policy.provider.maxOutputBytes}`,
      "--log-driver", "none", "--user", `${runtime.brokerUid}:${runtime.brokerGid}`, "--workdir", "/work",
      "--tmpfs", `/work:rw,nosuid,nodev,noexec,size=16m,mode=0700,uid=${runtime.brokerUid},gid=${runtime.brokerGid}`,
      "--tmpfs", `/run/pixel:rw,nosuid,nodev,noexec,size=80m,mode=0700,uid=${runtime.brokerUid},gid=${runtime.brokerGid}`,
      "--mount", `type=bind,source=${paths.output},destination=/run/pixel/out`,
      "--env", `PIXEL_CODEX_PROXY_URL=${proxyUrl}`, "--entrypoint", "/opt/node/bin/node", policy.transport.runnerImageRef,
      "/opt/pixel/deploy/work-codex-provider/container-entrypoint.mjs",
    ]),
  });
}

async function readFinalOutput(path, maximumBytes) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size < 2 || info.size > maximumBytes) fail("isolated Codex final output is not a bounded single-link regular file");
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail("isolated Codex final output could not be opened safely"));
  try {
    const opened = await handle.stat(); if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== info.dev || opened.ino !== info.ino || opened.size !== info.size) fail("isolated Codex final output changed during validation");
    const bytes = await handle.readFile(); if (bytes.includes(0)) fail("isolated Codex final output contains invalid bytes");
    try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail("isolated Codex final output is not strict UTF-8"); }
  } finally { await handle.close(); }
}

async function ensureNamedContainerRemoved({ docker, bootstrap, environment, containerName }) {
  await runCaptured({ command: docker, args: [...bootstrap, "rm", "--force", containerName], environment, timeoutMilliseconds: 5000, stdoutLimit: 128 * 1024 }).catch(() => {});
  const remaining = await runCaptured({
    command: docker,
    args: [...bootstrap, "ps", "--all", "--no-trunc", "--filter", `name=^/${containerName}$`, "--format", "{{.ID}}"],
    environment, timeoutMilliseconds: 5000, stdoutLimit: 128 * 1024,
  }).catch(() => fail("isolated Codex container cleanup could not be verified"));
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(remaining).trim(); } catch { fail("isolated Codex container cleanup evidence is invalid"); }
  if (text !== "") fail("isolated Codex container cleanup left a named container behind");
}

export function createIsolatedWorkCodexCliAdapter({ policy, credentialHandle, dockerPath, dockerBootstrapArguments = [], runtimeRoot, runtime, runtimeId, timeoutMilliseconds = null, allowNonLinuxTests = false }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy)); validateRuntime(runtime);
  if (!policy.enabled || typeof runtimeRoot !== "string" || !isAbsolute(runtimeRoot) || resolve(runtimeRoot) === parse(resolve(runtimeRoot)).root || /[,\r\n\u0000]/u.test(runtimeRoot) || !HEX_12.test(runtimeId ?? "") || process.platform !== "linux" && !allowNonLinuxTests) fail("isolated Codex adapter inputs are invalid or unsupported");
  let used = false;
  return Object.freeze({
    source: "codex-cli-isolated",
    async run(payload, { signal } = {}) {
      if (used) fail("isolated Codex adapter is single-use"); used = true; validateWorkCodexCliPayload(payload);
      if (payload.provider.transportSha256 !== sha(policy.transport) || payload.provider.authMode !== policy.provider.authMode || payload.provider.model !== policy.provider.model) fail("isolated Codex payload differs from private provider policy");
      const docker = await regularExecutable(dockerPath, "Docker executable"), bootstrap = [];
      for (const value of dockerBootstrapArguments) bootstrap.push(await regularExecutable(value, "Docker test bootstrap"));
      const root = resolve(runtimeRoot), paths = { root, output: join(root, "out"), dockerHome: join(root, "docker-home"), dockerConfig: join(root, "docker-config"), tmp: join(root, "tmp"), cidFile: join(root, "container.cid") };
      await mkdir(root, { mode: 0o700, recursive: false }).catch((error) => fail(`isolated Codex runtime could not be reserved: ${error.code ?? "mkdir-failed"}`));
      for (const path of [paths.output, paths.dockerHome, paths.dockerConfig, paths.tmp]) await mkdir(path, { mode: 0o700 });
      const environment = dockerEnvironment(paths), prefix = bootstrap;
      let frame = null, installRefresh = false;
      try {
        const egressPolicy = await privateJson(runtime.proxyPolicyPath, "Codex egress policy"); schema("Codex egress policy", validateWorkCodexEgressPolicy(egressPolicy));
        if (sha(egressPolicy) !== policy.transport.proxyPolicySha256 || canonical(egressPolicy.allowedHosts) !== canonical(policy.transport.allowedHosts)) fail("Codex egress policy differs from private provider policy");
        const [imageBytes, networkBytes, proxyBytes] = await Promise.all([
          runCaptured({ command: docker, args: [...prefix, "image", "inspect", policy.transport.runnerImageRef], environment }),
          runCaptured({ command: docker, args: [...prefix, "network", "inspect", runtime.networkName], environment }),
          runCaptured({ command: docker, args: [...prefix, "container", "inspect", runtime.proxyName], environment }),
        ]);
        validateIsolatedCodexImageInspection(parseDockerJson(imageBytes, "Codex runner image inspection"), policy);
        validateIsolatedCodexNetworkInspection(parseDockerJson(networkBytes, "Codex worker network inspection"), runtime);
        validateIsolatedCodexProxyInspection(parseDockerJson(proxyBytes, "Codex egress proxy inspection"), policy, runtime);
        const credential = policy.provider.authMode === "chatgpt" ? await readWorkCodexChatgptAuthCache({ policy, handle: credentialHandle }) : await readWorkCodexApiCredential({ policy, handle: credentialHandle });
        frame = encodeWorkCodexContainerFrame({ payload, credential });
        const command = buildIsolatedCodexDockerCommand({ dockerPath: docker, bootstrapArguments: bootstrap, policy, runtime, paths, runtimeId });
        const requestedTimeout = timeoutMilliseconds ?? payload.limits.timeoutSeconds * 1000;
        if (!Number.isInteger(requestedTimeout) || requestedTimeout < 1 || requestedTimeout > payload.limits.timeoutSeconds * 1000) fail("isolated Codex timeout must be a positive reduction of the exact plan limit");
        const stdoutLimit = Math.min(2 * 1024 * 1024, payload.limits.maxOutputBytes + 256 * 1024), outputPath = join(paths.output, "last-message.json");
        const stdout = await runCaptured({ command: command.executable, args: command.args, environment, stdin: frame, timeoutMilliseconds: requestedTimeout, stdoutLimit, signal, outputPath, outputLimit: payload.limits.maxOutputBytes });
        const outputText = await readFinalOutput(outputPath, payload.limits.maxOutputBytes); let transcript;
        try { transcript = parseCredentialFreeCodexCliTranscript({ stdout, outputText, maxOutputBytes: payload.limits.maxOutputBytes }); }
        catch { fail("isolated Codex transcript failed its exact no-tool contract"); }
        installRefresh = policy.provider.authMode === "chatgpt";
        return Object.freeze({ outputText, usage: transcript.usage, externalState: "invoked-once" });
      } finally {
        if (frame) frame.fill(0);
        let terminalError = null;
        try { await ensureNamedContainerRemoved({ docker, bootstrap, environment, containerName: `pixel-codex-${runtimeId}` }); } catch (error) { terminalError = error; }
        try { if (!terminalError && installRefresh) await replaceWorkCodexChatgptAuthCache({ policy, handle: credentialHandle, replacementPath: join(paths.output, "refreshed-auth.json"), suffix: runtimeId }); } catch (error) { terminalError ??= error; }
        try { await rm(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 20 }); } catch { terminalError ??= new IsolatedWorkCodexCliError("isolated Codex runtime cleanup failed"); }
        if (terminalError) throw terminalError;
      }
    },
  });
}
