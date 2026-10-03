#!/usr/bin/env node
import { createInterface } from "node:readline";

const scenario = process.argv[2] ?? "success";
const maxFrameBytes = Number(process.argv[3] ?? 1024);
const maxReassembledFrameBytes = Number(process.argv[4] ?? 4096);
let promptCount = 0;
let textCount = 0;
const send = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);
const tool = (index, toolName, args, result, isError = false) => {
  const toolCallId = `tool-${index}`;
  send({ type: "tool_execution_start", toolCallId, toolName, args });
  send({ type: "tool_execution_end", toolCallId, toolName, result, isError });
};

if (scenario === "bad-ready") send({ type: "ready", protocolVersion: 1, supportedProtocolVersions: [1] });
else send({ type: "ready", protocolVersion: 1, supportedProtocolVersions: [1, 2], maxFrameBytes, maxReassembledFrameBytes });
if (scenario === "builtin-widget") send({ type: "extension_ui_request", id: "widget-1", method: "setWidget", widgetKey: "autoresearch" });
if (scenario === "widget-content") send({ type: "extension_ui_request", id: "widget-1", method: "setWidget", widgetKey: "autoresearch", widgetLines: ["leak"] });
if (scenario === "widget-substitution") send({ type: "extension_ui_request", id: "widget-1", method: "setWidget", widgetKey: "attacker" });
if (scenario === "builtin-catalog") send({ type: "available_commands_update", commands: [{ name: "help", source: "builtin", aliases: ["h"], description: "Show help", input: { hint: "topic" }, subcommands: [{ name: "all", usage: "all" }, { name: "mm list", description: "List memory models" }] }, { name: "autoresearch", source: "extension" }, { name: "review", source: "file" }] });
if (scenario === "external-catalog") send({ type: "available_commands_update", commands: [{ name: "escape", source: "custom" }] });

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  const command = JSON.parse(line);
  if (command.type === "negotiate_protocol") {
    send({ id: command.id, type: "response", command: "negotiate_protocol", success: true, data: { protocolVersion: 2 } });
  } else if (command.type === "get_state") {
    const tools = ["task-async-update", "task-post-end-drift"].includes(scenario) ? ["read", "grep", "glob", "task"] : ["read", "grep", "glob"];
    send({ id: command.id, type: "response", command: "get_state", success: true, data: { dumpTools: tools.map((name) => ({ name })) } });
  } else if (command.type === "prompt") {
    promptCount += 1;
    if (["deferred-tool-recovery", "deferred-tool-ceiling"].includes(scenario)) {
      const forcedTool = promptCount === 1 ? "read" : promptCount === 2 ? "grep" : "glob";
      const expected = promptCount === 1
        ? "/force:read Inspect the immutable fixture."
        : `/force:${forcedTool} Continue the immutable objective by executing the ${forcedTool} call you just requested, using the exact arguments you already supplied. Then use the observed result and finish the objective.`;
      if (command.message !== expected) {
        send({ id: command.id, type: "response", command: "prompt", success: false, error: "deferred tool recovery was not bound" });
        process.exitCode = 1;
        return;
      }
      send({ type: "command_output", text: `Next turn forced to use ${forcedTool}.` });
      send({ id: command.id, type: "response", command: "prompt", success: true, data: { agentInvoked: true } });
      send({ type: "agent_start" });
      if (forcedTool === "read") tool(promptCount, forcedTool, { path: "src/main.js" }, { content: [] });
      else if (forcedTool === "grep") tool(promptCount, forcedTool, { path: "src", pattern: "needle" }, { content: [] });
      else tool(promptCount, forcedTool, { pattern: "**/*.js" }, { content: [] });
      send({ type: "agent_end" });
      return;
    }
    if (scenario === "expect-forced-read" && command.message !== "/force:read Inspect the immutable fixture.") {
      send({ id: command.id, type: "response", command: "prompt", success: false, error: "required first-turn tool was not bound" });
      process.exitCode = 1;
      return;
    }
    if (scenario === "expect-forced-read") send({ type: "command_output", text: "Next turn forced to use read." });
    if (scenario !== "events-first") send({ id: command.id, type: "response", command: "prompt", success: true, data: { agentInvoked: true } });
    if (scenario === "unexpected-tool") {
      send({ type: "tool_execution_start", toolCallId: "tool-1", toolName: "bash", args: { command: "id" } });
    } else if (scenario === "host-tool") {
      send({ type: "host_tool_call", id: "host-1", toolName: "escape", arguments: {} });
    } else if (scenario === "malformed") {
      process.stdout.write("{not-json}\n");
    } else if (scenario === "oversize") {
      process.stdout.write(`${" ".repeat(maxFrameBytes + 1)}\n`);
    } else if (scenario === "truncated") {
      process.stdout.write('{"type":"agent_end"}');
      process.exit(0);
    } else if (scenario === "hang") {
      setInterval(() => {}, 1000);
    } else if (scenario === "repeat-success") {
      for (let index = 1; index <= 3; index += 1) tool(index, "read", { path: "private/repeat.txt" }, { content: [{ type: "text", text: "unchanged-sensitive-result" }] });
      send({ type: "agent_end" });
    } else if (scenario === "repeat-error") {
      for (let index = 1; index <= 2; index += 1) tool(index, "grep", { path: "src", pattern: "needle" }, { content: [{ type: "text", text: "same-sensitive-error" }] }, true);
      send({ type: "agent_end" });
    } else if (scenario === "oscillation") {
      for (let index = 0; index < 6; index += 1) {
        const alternate = index % 2 === 1;
        tool(index + 1, alternate ? "grep" : "read", alternate ? { path: "b", pattern: "x" } : { path: "a" }, { content: [{ type: "text", text: alternate ? "B" : "A" }] });
      }
      send({ type: "agent_end" });
    } else if (scenario === "changed-result") {
      for (let index = 1; index <= 3; index += 1) tool(index, "read", { path: "progress.txt" }, { content: [{ type: "text", text: `revision-${index}` }] });
      send({ type: "agent_end" });
    } else if (scenario === "valid-update") {
      send({ type: "agent_start" });
      send({ type: "tool_execution_start", toolCallId: "tool-1", toolName: "read", args: { path: "src/main.js" } });
      send({ type: "tool_execution_update", toolCallId: "tool-1", toolName: "read", args: { path: "src/main.js" }, partialResult: { content: [] } });
      send({ type: "tool_execution_end", toolCallId: "tool-1", toolName: "read", result: { content: [] }, isError: false });
      send({ type: "agent_end" });
    } else if (scenario === "task-async-update") {
      send({ type: "agent_start" });
      send({ type: "tool_execution_start", toolCallId: "tool-1", toolName: "task", args: { context: "ctx", tasks: [{ agent: "task", task: "bounded" }] } });
      send({ type: "tool_execution_update", toolCallId: "tool-1", toolName: "task", args: {}, partialResult: { content: [{ type: "text", text: "running" }] } });
      send({ type: "tool_execution_end", toolCallId: "tool-1", toolName: "task", result: { content: [{ type: "text", text: "spawned" }] }, isError: false });
      send({ type: "tool_execution_update", toolCallId: "tool-1", toolName: "task", args: {}, partialResult: { content: [{ type: "text", text: "completed" }] } });
      send({ type: "agent_end" });
    } else if (scenario === "task-post-end-drift") {
      send({ type: "agent_start" });
      send({ type: "tool_execution_start", toolCallId: "tool-1", toolName: "task", args: { context: "ctx", tasks: [{ agent: "task", task: "bounded" }] } });
      send({ type: "tool_execution_end", toolCallId: "tool-1", toolName: "task", result: { content: [{ type: "text", text: "spawned" }] }, isError: false });
      send({ type: "tool_execution_update", toolCallId: "tool-1", toolName: "task", args: { context: "changed", tasks: [{ agent: "task", task: "widened" }] }, partialResult: { content: [] } });
    } else if (scenario === "nonterminal-agent-end") {
      send({ type: "agent_start" });
      tool(1, "read", { path: "src/first.js" }, { content: [] });
      send({ type: "agent_end", isTerminal: false });
      send({ type: "agent_start" });
      tool(2, "read", { path: "src/continued.js" }, { content: [] });
      send({ type: "agent_end", isTerminal: true });
    } else if (scenario === "malformed-agent-end") {
      send({ type: "agent_end", isTerminal: "false" });
    } else if (scenario === "mismatched-end") {
      send({ type: "tool_execution_start", toolCallId: "tool-1", toolName: "read", args: { path: "src/main.js" } });
      send({ type: "tool_execution_end", toolCallId: "tool-1", toolName: "grep", result: { content: [] }, isError: false });
    } else if (scenario === "reused-id") {
      tool(1, "read", { path: "src/main.js" }, { content: [] });
      send({ type: "tool_execution_start", toolCallId: "tool-1", toolName: "read", args: { path: "src/other.js" } });
    } else if (scenario === "unknown-update") {
      send({ type: "tool_execution_update", toolCallId: "tool-1", toolName: "read", args: { path: "src/main.js" }, partialResult: { content: [] } });
    } else if (scenario === "argument-drift") {
      send({ type: "tool_execution_start", toolCallId: "tool-1", toolName: "read", args: { path: "src/main.js" } });
      send({ type: "tool_execution_update", toolCallId: "tool-1", toolName: "read", args: { path: "private/other.js" }, partialResult: { content: [] } });
    } else if (scenario === "post-end-tool") {
      send({ type: "agent_end" });
      tool(1, "read", { path: "src/main.js" }, { content: [] });
    } else {
      send({ type: "agent_start" });
      send({ type: "tool_execution_start", toolCallId: "tool-1", toolName: "read", args: { path: "src/main.js" } });
      send({ type: "tool_execution_end", toolCallId: "tool-1", toolName: "read", result: { content: [] }, isError: false });
      send({ type: "agent_end" });
      if (scenario === "events-first") send({ id: command.id, type: "response", command: "prompt", success: true, data: { agentInvoked: true } });
    }
  } else if (command.type === "get_last_assistant_text") {
    textCount += 1;
    let text = "The fixture invariant is 42.";
    if (scenario === "empty-result") text = "";
    else if (["deferred-tool-recovery", "deferred-tool-ceiling"].includes(scenario) && textCount === 1) {
      text = JSON.stringify({ name: "grep", arguments: { path: "src", pattern: "needle" } });
    } else if (scenario === "deferred-tool-ceiling" && textCount > 1) {
      text = JSON.stringify({ name: "glob", arguments: { pattern: "**/*.js" } });
    }
    const data = scenario === "missing-result" ? {} : { text };
    send({ id: command.id, type: "response", command: "get_last_assistant_text", success: true, data });
  }
});
