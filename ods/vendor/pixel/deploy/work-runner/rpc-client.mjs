import { createHash } from "node:crypto";
import { spawn } from "node:child_process";

import { RpcJsonlDecoder, rpcFramingLimits } from "./rpc-framing.mjs";
import { RpcLoopGuardError, RpcObservationLoopGuard } from "./rpc-loop-guard.mjs";

const SAFE_EVENT_TYPES = new Set([
  "agent_start", "agent_end", "turn_start", "turn_end", "message_start", "message_update", "message_end",
  "tool_execution_start", "tool_execution_update", "tool_execution_end", "auto_compaction_start", "auto_compaction_end",
  "auto_retry_start", "auto_retry_end", "ttsr_triggered", "todo_reminder", "todo_auto_clear",
]);
const FORBIDDEN_FRAME_TYPES = new Set([
  "extension_ui_request", "host_tool_call", "host_tool_cancel", "host_uri_request", "host_uri_cancel", "extension_error",
]);

export class RpcClientError extends Error {}

function assertString(value, label, maximum) {
  if (typeof value !== "string" || value.length < 1 || Buffer.byteLength(value, "utf8") > maximum) {
    throw new RpcClientError(`${label} is empty or oversized`);
  }
}

function sameStrings(left, right) {
  return JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());
}

function deferredToolCall(text, allowedTools) {
  if (typeof text !== "string") return null;
  let candidate = text.trim();
  const fence = /^```(?:json)?\s*([\s\S]*?)\s*```$/iu.exec(candidate);
  if (fence) candidate = fence[1].trim();
  let value;
  try { value = JSON.parse(candidate); } catch { return null; }
  if (
    !value || typeof value !== "object" || Array.isArray(value)
    || !sameStrings(Object.keys(value), ["name", "arguments"])
    || typeof value.name !== "string" || !allowedTools.includes(value.name)
    || !value.arguments || typeof value.arguments !== "object" || Array.isArray(value.arguments)
  ) return null;
  return { name: value.name };
}

function isContentFreeBuiltinUiFrame(value) {
  return value?.type === "extension_ui_request"
    && value.method === "setWidget"
    && value.widgetKey === "autoresearch"
    && typeof value.id === "string"
    && /^[A-Za-z0-9_-]{1,64}$/.test(value.id)
    && sameStrings(Object.keys(value), ["type", "id", "method", "widgetKey"]);
}

function isBoundedRestrictedCommandCatalog(value) {
  if (!value || value.type !== "available_commands_update" || !Array.isArray(value.commands) || value.commands.length > 512 || !sameStrings(Object.keys(value), ["type", "commands"])) return false;
  const safeText = (text, maximum) => typeof text === "string" && Buffer.byteLength(text, "utf8") <= maximum;
  const safeName = (name) => safeText(name, 128) && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(name);
  const safeSubcommandName = (name) => safeText(name, 128) && /^[A-Za-z0-9][A-Za-z0-9_.: -]{0,127}$/.test(name);
  return value.commands.every((command) => {
    if (!command || typeof command !== "object" || Array.isArray(command) || !["builtin", "extension", "file"].includes(command.source) || !safeName(command.name)) return false;
    const keys = Object.keys(command);
    if (keys.some((key) => !["name", "source", "aliases", "description", "input", "subcommands"].includes(key))) return false;
    if (command.description !== undefined && !safeText(command.description, 4096)) return false;
    if (command.aliases !== undefined && (!Array.isArray(command.aliases) || command.aliases.length > 32 || !command.aliases.every(safeName))) return false;
    if (command.input !== undefined && (
      !command.input || typeof command.input !== "object" || Array.isArray(command.input)
      || Object.keys(command.input).some((key) => key !== "hint")
      || (command.input.hint !== undefined && !safeText(command.input.hint, 1024))
    )) return false;
    return command.subcommands === undefined || (
      Array.isArray(command.subcommands) && command.subcommands.length <= 128
      && command.subcommands.every((subcommand) => subcommand && typeof subcommand === "object" && !Array.isArray(subcommand)
        && Object.keys(subcommand).every((key) => ["name", "description", "usage"].includes(key))
        && safeSubcommandName(subcommand.name)
        && (subcommand.description === undefined || safeText(subcommand.description, 4096))
        && (subcommand.usage === undefined || safeText(subcommand.usage, 1024)))
    );
  });
}

export function runRpcSession(options) {
  assertString(options?.command, "RPC command", 4096);
  assertString(options?.cwd, "RPC working directory", 4096);
  if (!Array.isArray(options.args) || !options.args.every((value) => typeof value === "string")) throw new RpcClientError("RPC arguments are invalid");
  if (!options.env || typeof options.env !== "object" || Array.isArray(options.env)) throw new RpcClientError("RPC environment must be explicit");
  if (!Array.isArray(options.allowedTools) || options.allowedTools.length < 1 || new Set(options.allowedTools).size !== options.allowedTools.length) {
    throw new RpcClientError("RPC allowed tools are invalid");
  }
  const requiredInitialTool = options.requiredInitialTool;
  if (requiredInitialTool !== undefined && (
    typeof requiredInitialTool !== "string"
    || !/^[a-z][a-z0-9_]{0,63}$/u.test(requiredInitialTool)
    || !options.allowedTools.includes(requiredInitialTool)
  )) throw new RpcClientError("RPC required initial tool is invalid or unleased");
  const initialPrompt = requiredInitialTool === undefined
    ? options?.prompt
    : `/force:${requiredInitialTool} ${options?.prompt ?? ""}`;
  assertString(initialPrompt, "RPC prompt", options?.maxPromptBytes ?? 65536);
  const maxRuntimeMs = options.maxRuntimeMs;
  const maxToolCalls = options.maxToolCalls;
  const maxResultBytes = options.maxResultBytes;
  const maxStderrBytes = options.maxStderrBytes ?? 1024 * 1024;
  const terminationTimeoutMs = options.terminationTimeoutMs ?? 5000;
  const maxDeferredToolRecoveries = options.maxDeferredToolRecoveries ?? 0;
  const allowEmptyResult = options.allowEmptyResult ?? false;
  for (const [label, value] of Object.entries({ maxRuntimeMs, maxToolCalls, maxResultBytes, maxStderrBytes, terminationTimeoutMs })) {
    if (!Number.isSafeInteger(value) || value < 1) throw new RpcClientError(`${label} is invalid`);
  }
  if (!Number.isSafeInteger(maxDeferredToolRecoveries) || maxDeferredToolRecoveries < 0 || maxDeferredToolRecoveries > 8 || maxDeferredToolRecoveries >= maxToolCalls) {
    throw new RpcClientError("maxDeferredToolRecoveries is invalid");
  }
  if (typeof allowEmptyResult !== "boolean") throw new RpcClientError("allowEmptyResult is invalid");
  const transport = Object.freeze({
    maxFrameBytes: options.transport?.maxFrameBytes ?? rpcFramingLimits.maxFrameBytes,
    maxReassembledBytes: options.transport?.maxReassembledBytes ?? rpcFramingLimits.maxReassembledBytes,
    maxStreamBytes: options.transport?.maxStreamBytes ?? Math.min(rpcFramingLimits.maxStreamBytes, Math.max(rpcFramingLimits.maxFrameBytes, maxResultBytes * 8)),
  });
  const startedAt = Date.now();

  return new Promise((resolve, reject) => {
    const decoder = new RpcJsonlDecoder(transport);
    const stderrDigest = createHash("sha256");
    const observedTools = new Set();
    const loopGuard = new RpcObservationLoopGuard(options.loopGuard);
    let stderrBytes = 0;
    let frames = 0;
    let toolCalls = 0;
    let stage = "ready";
    let promptAcknowledged = false;
    let activeForcedTool = requiredInitialTool;
    let forcedToolAcknowledged = activeForcedTool === undefined;
    let activePromptId = "pixel-prompt";
    let deferredToolRecoveries = 0;
    let agentEnded = false;
    let settled = false;
    let pendingResult = null;
    let timedOut = false;
    const child = spawn(options.command, options.args, {
      cwd: options.cwd,
      env: options.env,
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });

    const timer = setTimeout(() => {
      timedOut = true;
      finishError(new RpcClientError("RPC executor exceeded its runtime ceiling"));
    }, maxRuntimeMs);
    timer.unref?.();

    function terminate() {
      child.stdin.destroy();
      if (!child.killed) child.kill("SIGKILL");
      try { return Promise.resolve(options.onTerminate?.()).catch(() => {}); } catch { return Promise.resolve(); }
    }

    function finishError(error) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const failure = error instanceof RpcClientError ? error : new RpcClientError("RPC executor failed closed");
      if (failure.protocolStage === undefined) failure.protocolStage = stage;
      if (failure.observedFrames === undefined) failure.observedFrames = frames;
      if (failure.stderrBytes === undefined) failure.stderrBytes = stderrBytes;
      let cleanupTimer;
      const cleanupDeadline = new Promise((resolve) => { cleanupTimer = setTimeout(resolve, terminationTimeoutMs); });
      Promise.race([terminate(), cleanupDeadline]).then(() => {
        clearTimeout(cleanupTimer);
        reject(failure);
      });
    }

    function send(value) {
      if (settled || child.stdin.destroyed || !child.stdin.writable) return finishError(new RpcClientError("RPC executor stdin closed unexpectedly"));
      const payload = `${JSON.stringify(value)}\n`;
      child.stdin.write(payload, (error) => {
        if (error) finishError(new RpcClientError("RPC executor command write failed"));
      });
    }

    function requestText() {
      if (stage === "text" || stage === "closing") return;
      if (loopGuard.activeCount !== 0) return finishError(new RpcClientError("RPC agent ended with unfinished tool calls"));
      stage = "text";
      send({ id: "pixel-text", type: "get_last_assistant_text" });
    }

    function response(frame) {
      if (frame.success !== true) return finishError(new RpcClientError("RPC executor rejected a bounded command"));
      if (frame.id === "pixel-protocol" && stage === "protocol" && frame.command === "negotiate_protocol") {
        stage = "state";
        send({ id: "pixel-state", type: "get_state" });
        return;
      }
      if (frame.id === "pixel-state" && stage === "state" && frame.command === "get_state") {
        const dumpTools = frame.data?.dumpTools;
        if (!Array.isArray(dumpTools) || !dumpTools.every((tool) => tool && typeof tool.name === "string")) {
          return finishError(new RpcClientError("RPC executor did not disclose its active tool surface"));
        }
        const names = dumpTools.map((tool) => tool.name);
        if (new Set(names).size !== names.length || !sameStrings(names, options.allowedTools)) {
          return finishError(new RpcClientError("RPC executor tool surface differs from the lease"));
        }
        stage = "prompt";
        send({ id: "pixel-prompt", type: "prompt", message: initialPrompt });
        return;
      }
      if (frame.id === activePromptId && frame.command === "prompt" && ["prompt", "running"].includes(stage)) {
        if (promptAcknowledged) return finishError(new RpcClientError("RPC executor repeated the prompt acknowledgement"));
        if (!forcedToolAcknowledged) return finishError(new RpcClientError("RPC executor did not acknowledge the required initial tool"));
        promptAcknowledged = true;
        if (frame.data?.agentInvoked === false || agentEnded) requestText();
        else stage = "running";
        return;
      }
      if (frame.id === "pixel-text" && stage === "text" && frame.command === "get_last_assistant_text") {
        const text = frame.data?.text == null && allowEmptyResult ? "" : frame.data?.text;
        if (typeof text !== "string") return finishError(new RpcClientError("RPC result is invalid"));
        const resultBytes = Buffer.byteLength(text, "utf8");
        if (resultBytes > maxResultBytes) return finishError(new RpcClientError("RPC result is oversized"));
        if (resultBytes === 0 && !allowEmptyResult) return finishError(new RpcClientError("RPC result is empty"));
        const deferred = deferredToolCall(text, options.allowedTools);
        if (deferred && maxDeferredToolRecoveries > 0) {
          if (deferredToolRecoveries >= maxDeferredToolRecoveries) {
            return finishError(new RpcClientError("RPC executor exceeded its deferred tool-call recovery ceiling"));
          }
          deferredToolRecoveries += 1;
          activeForcedTool = deferred.name;
          forcedToolAcknowledged = false;
          promptAcknowledged = false;
          agentEnded = false;
          activePromptId = `pixel-recovery-${deferredToolRecoveries}`;
          stage = "prompt";
          send({
            id: activePromptId,
            type: "prompt",
            message: `/force:${deferred.name} Continue the immutable objective by executing the ${deferred.name} call you just requested, using the exact arguments you already supplied. Then use the observed result and finish the objective.`,
          });
          return;
        }
        pendingResult = text;
        stage = "closing";
        child.stdin.end();
        return;
      }
      finishError(new RpcClientError("RPC executor emitted an unexpected command response"));
    }

    function frame(value) {
      if (settled) return;
      frames += 1;
      if (frames > 100000) return finishError(new RpcClientError("RPC executor exceeded its frame-count ceiling"));
      if (stage === "ready") {
        if (
          value.type !== "ready"
          || value.protocolVersion !== 1
          || !sameStrings(value.supportedProtocolVersions ?? [], [1, 2])
          || value.maxFrameBytes !== transport.maxFrameBytes
          || value.maxReassembledFrameBytes !== transport.maxReassembledBytes
          || !sameStrings(Object.keys(value), ["type", "protocolVersion", "supportedProtocolVersions", "maxFrameBytes", "maxReassembledFrameBytes"])
        ) return finishError(new RpcClientError("RPC executor ready contract differs from the pinned protocol"));
        stage = "protocol";
        send({ id: "pixel-protocol", type: "negotiate_protocol", protocolVersion: 2 });
        return;
      }
      if (value.type === "ready") return finishError(new RpcClientError("RPC executor repeated its ready frame"));
      if (isContentFreeBuiltinUiFrame(value)) return;
      if (value.type === "available_commands_update") {
        if (!isBoundedRestrictedCommandCatalog(value)) {
          const sources = Array.isArray(value.commands)
            ? [...new Set(value.commands.map((command) => command?.source).filter((source) => typeof source === "string" && /^[a-z_]{1,32}$/.test(source)))].sort().join(",")
            : "invalid";
          return finishError(new RpcClientError(`RPC executor command catalog widened beyond packaged sources (sources=${sources || "missing"})`));
        }
        return;
      }
      if (value.type === "command_output") {
        const expected = activeForcedTool === undefined ? null : `Next turn forced to use ${activeForcedTool}.`;
        if (
          stage !== "prompt" || forcedToolAcknowledged || value.text !== expected
          || !sameStrings(Object.keys(value), ["type", "text"])
        ) return finishError(new RpcClientError("RPC executor emitted unexpected command output"));
        forcedToolAcknowledged = true;
        return;
      }
      if (FORBIDDEN_FRAME_TYPES.has(value.type)) return finishError(new RpcClientError(`RPC executor requested forbidden capability ${value.type}`));
      if (value.type === "response") return response(value);
      if (!SAFE_EVENT_TYPES.has(value.type)) {
        const type = typeof value.type === "string" && /^[a-z_]{1,64}$/.test(value.type) ? value.type : "invalid";
        return finishError(new RpcClientError(`RPC executor emitted unsupported frame type ${type}`));
      }
      if (!["prompt", "running", "text"].includes(stage)) return finishError(new RpcClientError("RPC executor emitted an event before prompt authorization"));
      if (agentEnded && value.type.startsWith("tool_execution_")) {
        return finishError(new RpcClientError("RPC executor emitted a tool event after agent completion"));
      }
      if (value.type === "tool_execution_start") {
        if (!options.allowedTools.includes(value.toolName)) {
          return finishError(new RpcClientError("RPC executor attempted an unleased or malformed tool call"));
        }
        toolCalls += 1;
        if (toolCalls > maxToolCalls) return finishError(new RpcClientError("RPC executor exceeded its tool-call ceiling"));
        try { loopGuard.start(value); } catch (error) {
          return finishError(new RpcClientError(error instanceof RpcLoopGuardError ? error.message : "RPC tool start failed closed"));
        }
        observedTools.add(value.toolName);
      } else if (value.type === "tool_execution_update") {
        try { loopGuard.update(value); } catch (error) {
          return finishError(new RpcClientError(error instanceof RpcLoopGuardError ? error.message : "RPC tool update failed closed"));
        }
      } else if (value.type === "tool_execution_end") {
        let receipt;
        try { receipt = loopGuard.end(value); } catch (error) {
          return finishError(new RpcClientError(error instanceof RpcLoopGuardError ? error.message : "RPC tool end failed closed"));
        }
        if (receipt.status === "stopped") {
          const failure = new RpcClientError(`RPC executor stopped by observation loop guard (${receipt.reason})`);
          failure.loopGuard = receipt;
          return finishError(failure);
        }
      } else if (value.type === "agent_end") {
        if (value.isTerminal !== undefined && typeof value.isTerminal !== "boolean") {
          return finishError(new RpcClientError("RPC executor emitted a malformed completion marker"));
        }
        if (value.isTerminal === false) return;
        if (agentEnded) return finishError(new RpcClientError("RPC executor repeated agent completion"));
        agentEnded = true;
        if (promptAcknowledged) requestText();
      }
    }

    child.stdout.on("data", (bytes) => {
      if (settled) return;
      try {
        for (const value of decoder.push(bytes)) frame(value);
      } catch (error) {
        finishError(new RpcClientError(`RPC framing violation: ${error.message}`));
      }
    });
    child.stderr.on("data", (bytes) => {
      if (settled) return;
      stderrBytes += bytes.length;
      if (stderrBytes > maxStderrBytes) return finishError(new RpcClientError("RPC executor stderr exceeded its byte ceiling"));
      stderrDigest.update(bytes);
    });
    child.on("error", () => finishError(new RpcClientError("RPC executor could not be started")));
    child.stdin.on("error", () => {
      if (!settled && stage !== "closing") finishError(new RpcClientError("RPC executor stdin failed"));
    });
    child.on("close", (code, signal) => {
      if (settled) return;
      try {
        decoder.end();
      } catch (error) {
        return finishError(new RpcClientError(`RPC framing violation: ${error.message}`));
      }
      if (timedOut || stage !== "closing" || pendingResult === null || code !== 0 || signal !== null) {
        const failure = new RpcClientError("RPC executor exited before a valid bounded result");
        failure.exitCode = Number.isSafeInteger(code) ? code : null;
        failure.exitSignal = typeof signal === "string" && /^[A-Z0-9]{1,16}$/u.test(signal) ? signal : null;
        failure.protocolStage = stage;
        failure.observedFrames = frames;
        failure.stderrBytes = stderrBytes;
        return finishError(failure);
      }
      settled = true;
      clearTimeout(timer);
      resolve({
        text: pendingResult,
        frames,
        toolCalls,
        observedTools: [...observedTools].sort(),
        stderrBytes,
        stderrSha256: stderrDigest.digest("hex"),
        protocolVersion: 2,
        durationMilliseconds: Math.max(0, Date.now() - startedAt),
        deferredToolRecoveries,
        loopGuard: loopGuard.receipt(),
      });
    });
  });
}
