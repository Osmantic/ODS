import { canonical, validateWorkModelArtifactManifest, validateWorkModelBackendConfig, validateWorkModelBackendLaunch, validateWorkModelRuntimeCacheManifest } from "../../scripts/lib/work-contract.mjs";
import { modelBackendIdentityLabels, modelBackendNetworkIdentityLabels } from "../work-runner/docker-boundary.mjs";
import { buildModelBackendLaunch, modelBackendRuntimeCacheRoot } from "./model-backend-launch.mjs";

const ENV_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/u;
const CREDENTIAL_ENV_RE = /^(?:.*_)?(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_?KEY|ACCESS_?KEY|AUTHORIZATION|COOKIE)(?:_(?:FILE|PATH|VALUE|JSON|B64|BASE64|URI|URL))?$/iu;
const PROXY_ENV = new Set(["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]);
const NO_PROXY_ENV = new Set(["NO_PROXY"]);

export class WorkModelBackendRuntimeError extends Error {}
function fail(message) { throw new WorkModelBackendRuntimeError(message); }
function same(left, right) { return canonical(left) === canonical(right); }
function empty(value) { return value === null || value === undefined || Array.isArray(value) && value.length === 0 || value === ""; }
function emptyRecord(value) { return value === null || value === undefined || typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0; }

function optionValues(args, option) {
  const values = [];
  for (let index = 0; index < args.length; index += 1) if (args[index] === option) {
    if (index + 1 >= args.length) fail("model backend launch option is incomplete");
    values.push(args[index + 1]); index += 1;
  }
  return values;
}

function environmentMap(values, label) {
  if (!Array.isArray(values)) fail(`${label} is invalid`);
  const result = new Map();
  for (const entry of values) {
    if (typeof entry !== "string") fail(`${label} contains a non-string entry`);
    const separator = entry.indexOf("="), name = entry.slice(0, separator), value = entry.slice(separator + 1);
    if (separator < 1 || !ENV_NAME_RE.test(name) || result.has(name)) fail(`${label} contains an invalid or duplicate variable`);
    // Some pinned runtime images declare empty credential placeholders (for
    // example HF_TOKEN=) in their immutable image environment. An empty value
    // carries no credential and must remain inspectable so an exact failed
    // start can be rolled back. Any populated credential-shaped variable is
    // still rejected, whether it came from the image or an explicit override.
    if (CREDENTIAL_ENV_RE.test(name) && value !== "") fail(`${label} contains a credential-bearing variable`);
    const upper = name.toUpperCase();
    if (PROXY_ENV.has(upper) && value !== "") fail(`${label} contains an enabled ambient proxy`);
    if (NO_PROXY_ENV.has(upper) && value !== "*") fail(`${label} does not disable proxy routing globally`);
    result.set(name, value);
  }
  return result;
}

function expectedEnvironment(image, launch) {
  const expected = environmentMap(image.Config?.Env ?? [], "model backend image environment");
  const explicit = environmentMap(optionValues(launch.container.args, "--env"), "model backend explicit environment");
  for (const [name, value] of explicit) expected.set(name, value);
  return expected;
}

function normalizedTmpfs(value, label) {
  if (typeof value !== "string" || !value) fail(`${label} is invalid`);
  const entries = value.split(",");
  if (entries.some((entry) => !entry) || new Set(entries).size !== entries.length) fail(`${label} has duplicate or empty options`);
  return [...entries].sort();
}

function validateFilesystem(container, configuration) {
  const target = configuration.providerOptions.provider === "llama.cpp" ? "/models/model.gguf" : "/models/model";
  const cacheMounts = configuration.runtimeCacheSeed === null ? [] : [{
    source: configuration.runtimeCacheSeed.path, target: "/var/cache/pixel-model-seed",
  }];
  const expectedVisible = [
    { Type: "bind", Source: configuration.modelSource.path, Destination: target, RW: false },
    ...cacheMounts.map((mount) => ({ Type: "bind", Source: mount.source, Destination: mount.target, RW: false })),
  ].sort((left, right) => left.Destination.localeCompare(right.Destination, "en"));
  const mounts = container.Mounts;
  if (!Array.isArray(mounts)) fail("model backend visible filesystem mounts are invalid");
  const visible = mounts.filter((mount) => mount?.Type !== "tmpfs").map((mount) => ({ Type: mount?.Type, Source: mount?.Source, Destination: mount?.Destination, RW: mount?.RW })).sort((left, right) => String(left.Destination).localeCompare(String(right.Destination), "en"));
  if (!same(visible, expectedVisible)) fail("model backend visible mounts differ from the exact read-only sources for the model and runtime cache");
  const expectedHost = [
    { Type: "bind", Source: configuration.modelSource.path, Target: target, ReadOnly: true },
    ...cacheMounts.map((mount) => ({ Type: "bind", Source: mount.source, Target: mount.target, ReadOnly: true })),
  ].sort((left, right) => left.Target.localeCompare(right.Target, "en"));
  const hostMounts = container.HostConfig?.Mounts;
  if (!Array.isArray(hostMounts)) fail("model backend host mount request is invalid");
  const requested = hostMounts.map((mount) => ({ Type: mount?.Type, Source: mount?.Source, Target: mount?.Target, ReadOnly: mount?.ReadOnly })).sort((left, right) => String(left.Target).localeCompare(String(right.Target), "en"));
  if (!same(requested, expectedHost)) fail("model backend host mounts differ from the exact read-only sources for the model and runtime cache");
  if (!empty(container.HostConfig?.Binds) || !empty(container.HostConfig?.VolumesFrom) || container.HostConfig?.VolumeDriver) fail("model backend has an ambient bind, inherited volume, or volume driver");
  const uid = configuration.containerUser.uid, gid = configuration.containerUser.gid;
  const runtimeCacheRoot = modelBackendRuntimeCacheRoot(configuration);
  const expected = {
    "/tmp": `rw,nosuid,nodev,noexec,size=${configuration.resources.tmpfsMiB}m,mode=1777`,
    [runtimeCacheRoot]: `rw,nosuid,nodev,${configuration.providerOptions.provider === "vllm" ? "exec" : "noexec"},size=${configuration.resources.cacheMiB}m,mode=0700,uid=${uid},gid=${gid}`,
  };
  const actual = container.HostConfig?.Tmpfs;
  if (!actual || typeof actual !== "object" || Array.isArray(actual) || !same(Object.keys(actual).sort(), Object.keys(expected).sort())) fail("model backend tmpfs destinations differ from the exact contract");
  for (const [path, options] of Object.entries(expected)) if (!same(normalizedTmpfs(actual[path], `model backend ${path} tmpfs`), normalizedTmpfs(options, `expected ${path} tmpfs`))) fail("model backend tmpfs options differ from the exact contract");
}

function validateEnvironment(container, image, launch, configuration, policy) {
  const actual = environmentMap(container.Config?.Env ?? [], "model backend effective environment");
  const expected = expectedEnvironment(image, launch);
  if (!same([...actual.entries()].sort(), [...expected.entries()].sort())) fail("model backend effective environment differs from the image plus exact safe overrides");
  if (configuration.runtimeCacheSeed !== null) {
    const imageEnvironment = environmentMap(image.Config?.Env ?? [], "model backend image environment");
    if (imageEnvironment.get("LOCAL_INFERENCE_CACHE_FINGERPRINT") !== configuration.runtimeCacheSeed.cacheFingerprint) fail("runtime cache seed fingerprint differs from the pinned backend image");
  }
  if (container.Config?.User !== `${configuration.containerUser.uid}:${configuration.containerUser.gid}`) fail("model backend user differs from the exact non-root identity");
  if (container.Config?.Hostname !== "pixel-local-model" || container.Config?.Tty === true || container.Config?.OpenStdin === true || container.Config?.StdinOnce === true) fail("model backend interactive or hostname settings differ from the exact contract");
  if (container.Config?.StopSignal !== "SIGTERM" || container.Config?.StopTimeout !== 30) fail("model backend stop behavior differs from the exact contract");
  const expectedEntrypoint = configuration.providerOptions.provider === "vllm" ? ["/bin/sh"] : image.Config?.Entrypoint ?? null;
  if (!same(container.Config?.Entrypoint ?? null, expectedEntrypoint) || (container.Config?.WorkingDir ?? "") !== (image.Config?.WorkingDir ?? "")) fail("model backend entrypoint or working directory differs from the exact launch contract");
  const containerShell = container.Config?.Shell ?? null;
  if (!same(container.Config?.Healthcheck ?? null, image.Config?.Healthcheck ?? null)
    || !empty(containerShell) && !same(containerShell, image.Config?.Shell ?? null)) fail("model backend overrides the pinned image healthcheck or shell");
  const expectedExposedPorts = { ...(image.Config?.ExposedPorts ?? {}), ...(configuration.publishLoopbackPort === null ? {} : { "8080/tcp": {} }) };
  if (!same(container.Config?.ExposedPorts ?? {}, expectedExposedPorts) || !["", null, undefined].includes(container.Config?.MacAddress)) fail("model backend exposed-port or MAC configuration differs from the exact contract");
  const expectedLabels = { ...(image.Config?.Labels ?? {}), ...modelBackendIdentityLabels({ policy, bindings: launch.bindings }) };
  if (!same(container.Config?.Labels ?? {}, expectedLabels)) fail("model backend labels differ from the pinned image plus exact Pixel bindings");
}

function validateResources(container, configuration) {
  const host = container.HostConfig ?? {}, memory = configuration.resources.memoryMiB * 1024 * 1024;
  if (host.Memory !== memory || host.MemorySwap !== memory || host.NanoCpus !== configuration.resources.cpuCores * 1_000_000_000 || host.PidsLimit !== configuration.resources.pids || host.ShmSize !== configuration.resources.sharedMemoryMiB * 1024 * 1024) fail("model backend resource ceilings differ from the exact contract");
  if (!same(host.Ulimits, [{ Name: "nofile", Hard: configuration.resources.nofile, Soft: configuration.resources.nofile }])) fail("model backend file-descriptor ceiling differs from the exact contract");
  if (!same(host.RestartPolicy, { Name: configuration.restartPolicy ?? "unless-stopped", MaximumRetryCount: 0 })
    || !same(host.LogConfig, { Type: "local", Config: { "compress": "false", "max-file": "1", "max-size": "1m" } })) fail("model backend restart or bounded log retention differs from the exact contract");
  if (host.IpcMode !== "private" || host.CgroupnsMode !== "private" || host.Init !== true || host.AutoRemove !== false) fail("model backend process namespaces or init behavior differ from the exact contract");
  if (!empty(host.CapAdd) || !same(host.CapDrop, ["ALL"]) || !same(host.SecurityOpt, ["no-new-privileges:true"]) || host.Privileged !== false || host.ReadonlyRootfs !== true) fail("model backend hardened privilege boundary differs from the exact contract");
  if (!empty(host.Links) || !empty(host.Dns) || !empty(host.DnsOptions) || !empty(host.DnsSearch) || !empty(host.ExtraHosts) || !empty(host.GroupAdd) || !empty(host.DeviceCgroupRules)) fail("model backend has ambient host, DNS, group, link, or device configuration");
  if (host.PidMode !== "" || host.UTSMode !== "" || host.UsernsMode !== "" || !["", "default"].includes(host.Isolation ?? "")) fail("model backend joins an unexpected host namespace");
  if (!["", "runc", ...(configuration.accelerator.class === "nvidia-cuda" ? ["nvidia"] : [])].includes(host.Runtime ?? "")) fail("model backend uses an unexpected container runtime");
  if (![false, null, undefined].includes(host.OomKillDisable) || (host.OomScoreAdj ?? 0) !== 0 || (host.MemoryReservation ?? 0) !== 0) fail("model backend weakens bounded memory failure behavior");
  const zeroFields = ["CpuShares", "CpuPeriod", "CpuQuota", "CpuRealtimePeriod", "CpuRealtimeRuntime", "BlkioWeight", "KernelMemory", "KernelMemoryTCP", "MemorySwappiness", "CpuCount", "CpuPercent", "IOMaximumIOps", "IOMaximumBandwidth"];
  if (zeroFields.some((name) => ![0, null, undefined].includes(host[name]))
    || !["", null, undefined].includes(host.CgroupParent) || !["", null, undefined].includes(host.CpusetCpus) || !["", null, undefined].includes(host.CpusetMems)
    || !emptyRecord(host.Sysctls) || !emptyRecord(host.StorageOpt)
    || ["BlkioWeightDevice", "BlkioDeviceReadBps", "BlkioDeviceWriteBps", "BlkioDeviceReadIOps", "BlkioDeviceWriteIOps"].some((name) => !empty(host[name]))) fail("model backend has an ambient cgroup, sysctl, storage, or I/O override");
  const requests = host.DeviceRequests ?? [];
  if (!empty(host.Devices)) fail("model backend has a direct host-device grant");
  if (configuration.accelerator.class === "cpu") {
    if (requests.length !== 0) fail("CPU model backend has a device request");
  } else {
    const request = requests[0], allowed = new Set(["Driver", "Count", "DeviceIDs", "Capabilities", "Options"]);
    if (requests.length !== 1 || Object.keys(request ?? {}).some((name) => !allowed.has(name))
      || !same({ Driver: request?.Driver, Count: request?.Count, DeviceIDs: request?.DeviceIDs ?? [], Capabilities: request?.Capabilities, Options: request?.Options ?? {} },
        { Driver: "nvidia", Count: configuration.accelerator.count, DeviceIDs: [], Capabilities: [["gpu"]], Options: {} })) fail("NVIDIA model backend device request differs from the exact contract");
  }
}

function validateCommand(container, launch, policy, configuration, environment, requireEffectivePorts = true) {
  const args = launch.container.args, imageIndexes = args.flatMap((value, index) => value === policy.localModel.imageRef ? [index] : []);
  if (imageIndexes.length !== 1) fail("model backend launch has an ambiguous image boundary");
  if (container.Config?.Image !== policy.localModel.imageRef || !same(container.Config?.Cmd, args.slice(imageIndexes[0] + 1))) fail("model backend image reference or server arguments differ from the exact launch");
  if (container.HostConfig?.NetworkMode !== environment.runtime.backendNetworkName || container.HostConfig?.PublishAllPorts !== false) fail("model backend network attachment or publication mode differs from the exact launch");
  const expectedPorts = configuration.publishLoopbackPort === null ? {} : { "8080/tcp": [{ HostIp: "127.0.0.1", HostPort: String(configuration.publishLoopbackPort) }] };
  if (!same(container.HostConfig?.PortBindings ?? {}, expectedPorts)) fail("model backend host port binding differs from the exact loopback contract");
  const effectivePorts = container.NetworkSettings?.Ports ?? {};
  if (configuration.publishLoopbackPort === null) {
    if (Object.values(effectivePorts).some((bindings) => Array.isArray(bindings) && bindings.length > 0)) fail("model backend has an effective host port outside the exact contract");
  } else if (Object.entries(effectivePorts).some(([port, bindings]) => port !== "8080/tcp" && Array.isArray(bindings) && bindings.length > 0)
    || Array.isArray(effectivePorts["8080/tcp"]) && effectivePorts["8080/tcp"].length > 0 && !same(effectivePorts["8080/tcp"], expectedPorts["8080/tcp"])
    || requireEffectivePorts && !same(effectivePorts["8080/tcp"] ?? null, expectedPorts["8080/tcp"])) {
    fail("model backend requested loopback port is not effectively bound by Docker");
  }
}

function ipv4(value) {
  if (typeof value !== "string" || !/^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$/u.test(value)) fail("model backend network contains invalid IPv4");
  const parts = value.split(".").map(Number); if (parts.some((part) => part > 255)) fail("model backend network contains invalid IPv4");
  return parts.reduce((total, part) => ((total << 8) | part) >>> 0, 0);
}

function subnet(value) {
  const [address, prefixText] = value.split("/"), prefix = Number(prefixText), network = ipv4(address), mask = (0xffffffff << (32 - prefix)) >>> 0;
  if (((network & mask) >>> 0) !== network) fail("model backend subnet is not aligned");
  return { prefix, network, broadcast: (network | (~mask >>> 0)) >>> 0, gateway: (network + 1) >>> 0 };
}

function validateNetwork(network, container, configuration, environment, policy, launch, requireAddress = true, requireAttachment = true) {
  const selected = subnet(configuration.backendNetwork.subnet);
  if (network.Scope !== "local" || network.EnableIPv4 === false || network.EnableIPv6 === true || network.ConfigOnly === true) fail("model backend network scope or IP family differs from the exact contract");
  if (!same(network.Labels ?? {}, modelBackendNetworkIdentityLabels({ policy, bindings: launch.bindings }))) fail("model backend network labels differ from the exact contract");
  const ipam = network.IPAM;
  const wantedIpam = [{ Subnet: configuration.backendNetwork.subnet, Gateway: `${selected.gateway >>> 24}.${selected.gateway >>> 16 & 255}.${selected.gateway >>> 8 & 255}.${selected.gateway & 255}` }];
  if (ipam?.Driver !== "default" || !same(ipam.Options ?? {}, {}) || !same(ipam.Config, wantedIpam)) fail("model backend network subnet or gateway differs from the exact contract");
  const options = network.Options ?? {};
  const allowedOptions = new Set(["com.docker.network.enable_ipv4", "com.docker.network.enable_ipv6"]);
  if (Object.keys(options).some((name) => !allowedOptions.has(name))
    || Object.hasOwn(options, "com.docker.network.enable_ipv4") && options["com.docker.network.enable_ipv4"] !== "true"
    || options["com.docker.network.enable_ipv6"] === "true") fail("model backend network has an unexpected driver option");
  if (!requireAttachment) return;
  const attachment = container?.NetworkSettings?.Networks?.[environment.runtime.backendNetworkName];
  if (!attachment || !same(attachment.Aliases, ["pixel-local-model"])) fail("model backend network attachment differs from the exact alias");
  if (requireAddress) {
    const address = ipv4(attachment.IPAddress), prefix = attachment.IPPrefixLen;
    if (prefix !== selected.prefix || address <= selected.gateway || address >= selected.broadcast || ((address & (0xffffffff << (32 - prefix))) >>> 0) !== selected.network) fail("model backend container address differs from the exact private subnet");
  } else if (!["", null, undefined].includes(attachment.IPAddress) || (attachment.IPPrefixLen ?? 0) !== 0) fail("dormant model backend unexpectedly retains a private address");
  if (attachment?.IPAMConfig !== null || !empty(attachment?.Links) || !empty(attachment?.DriverOpts)) fail("model backend network attachment has an ambient address, link, or driver override");
}

export function validateConfiguredModelBackendContainerInspect(container, image, { configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest, launch }, { requireEffectivePorts = container?.State?.Running === true } = {}) {
  const configErrors = validateWorkModelBackendConfig(configuration), artifactErrors = validateWorkModelArtifactManifest(artifactManifest), cacheErrors = runtimeCacheArtifactManifest === null ? [] : validateWorkModelRuntimeCacheManifest(runtimeCacheArtifactManifest), launchErrors = validateWorkModelBackendLaunch(launch);
  if (configErrors.length || artifactErrors.length || cacheErrors.length || launchErrors.length) fail("model backend exact inspection inputs are invalid");
  const rebuilt = buildModelBackendLaunch({ configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest });
  if (!same(rebuilt, launch)) fail("model backend launch differs from the exact private inputs");
  if (container?.Name !== `/${environment.runtime.backendContainerName}` || container?.Image !== image?.Id) fail("model backend container name or image identity differs from the exact contract");
  const networks = container.NetworkSettings?.Networks;
  if (!networks || !same(Object.keys(networks), [environment.runtime.backendNetworkName])) fail("model backend container network set differs from the exact contract");
  validateCommand(container, launch, policy, configuration, environment, requireEffectivePorts);
  validateFilesystem(container, configuration);
  validateEnvironment(container, image, launch, configuration, policy);
  validateResources(container, configuration);
  return true;
}

export function validateConfiguredModelBackendNetworkInspect(network, container, inputs, { requireAddress = true, requireAttachment = true } = {}) {
  validateNetwork(network, container, inputs.configuration, inputs.environment, inputs.policy, inputs.launch, requireAddress, requireAttachment);
  return true;
}

export function validateConfiguredModelBackendInspect(container, image, network, { configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest, launch }, { requireEffectivePorts = true } = {}) {
  const inputs = { configuration, environment, policy, artifactManifest, runtimeCacheArtifactManifest, launch };
  validateConfiguredModelBackendContainerInspect(container, image, inputs, { requireEffectivePorts });
  validateConfiguredModelBackendNetworkInspect(network, container, inputs);
  return true;
}
