"""Reviewed added vectors against the already qualified ODS 2026.6.33 SDK.

This development fixture writes only an explicitly selected private SDK copy.
Production repair recipes and their original-source custody remain separate.
"""
import argparse
import hashlib
import json
from pathlib import Path


READ_DEPENDENCIES = {
    # Read-only here; diagnostic-events is the earlier reviewed repair's FINAL
    # canonical byte hash, not an upstream/pristine-source hash.
    "attempt.model-diagnostic-events-DqqiPQPY.js": "efeb0c2cf6936ebbb3104e155a92495b529092f21b1211efe3e24b3695ae590b",
    "provider-stream-BXiTMl1u.js": "391a1245821d07fdf51a42f109f35ee6353b531979cdfc9bfd63535fb92a8e15",
    "openai-transport-stream-P3cLoEh2.js": "3518e7a4cbcd178eaca8c8f1f3a1d552602a2a05349249f150520871aadea3d6",
    "openai-prompt-cache-Ki7_LdG-.js": "9f34caed1cee6d161e2ed00a6b025852b413bc8a7259cad2f21d85e7dee81b26",
    "provider-stream-shared-DwdPV7LM.js": "bd3995d7fcfe613ccc355ee64468795825ae1b8fdb5973e2f18179065bc4335a",
    "session-write-lock-qJaTPAUi.js": "1a1d85debdfd25a0eab194b7264f2522bc84ba44437f9b1338e83c474c4ddfc0",
    "file-lock-D6zEe-ZP.js": "a1d01df1fe41ea1bb750359c3b14576f7ce4997e2f85da3becc2a6cdc39a9a28",
    "sidecar-lock-Bv7C32CP.js": "5303cdd5d3d31e2a912e33f5a7805914d9a980345af09cb9369956c988a7a0ba",
}


HELPER = r'''// ODS native revision tool scope v1. This is a permission ceiling, not a grant.
const odsRetryScopes = new WeakMap();
const odsRetryScopeTokens = new WeakSet();
const odsRetryIdentityKeys = ["runId", "sessionId", "sessionKey", "agentId", "lifecycleGeneration"];
function odsRetryScopeError() { return new Error("ODS native revision tool scope is unavailable or inconsistent; no broader retry is permitted."); }
function odsRejectRetryToolScope(scope) {
 if (scope && odsRetryScopeTokens.has(scope)) { scope.restricted = true; scope.closed = true; }
 throw odsRetryScopeError();
}
function odsNormalizeRetryToolsAllow(value) {
 if (!Array.isArray(value) || value.length < 1 || value.length > 32 ||
     value.some(name => typeof name !== "string" || !/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(name)) ||
     new Set(value).size !== value.length) throw odsRetryScopeError();
 return Object.freeze([...value]);
}
function odsMeetRetryToolsAllow(values) {
 let result;
 for (const value of values) {
  const next = odsNormalizeRetryToolsAllow(value);
  result = result === void 0 ? [...next] : result.filter(name => next.includes(name));
  if (!result.length) throw odsRetryScopeError();
 }
 return result === void 0 ? void 0 : Object.freeze(result);
}
function odsAcquireRetryToolScope(owner, identity, current, priorOwner) {
 if (!owner || typeof owner !== "object") return;
 const previous = odsRetryScopes.get(owner);
 const carried = priorOwner && priorOwner !== owner ? odsRetryScopes.get(priorOwner) : void 0;
 if (carried) {
  // A native lifecycle/context replacement cannot manufacture an uncapped
  // continuation. Retain a closed marker in the replacement's native custody.
  carried.closed = true;
  if (previous && previous !== carried) odsRejectRetryToolScope(previous);
  odsRetryScopes.set(owner, carried);
  if (carried.restricted) throw odsRetryScopeError();
  return;
 }
 const valid = typeof current === "function" && !odsRetryIdentityKeys.some(key =>
  typeof identity?.[key] !== "string" || !identity[key] || identity[key].length > 2048);
 if (previous) {
  if (previous.closed || !valid || odsRetryIdentityKeys.some(key => previous.identity[key] !== identity[key])) {
   if (previous.restricted) throw odsRetryScopeError();
   previous.closed = true;
   return;
  }
  previous.current = current;
  try { odsAssertRetryToolScope(previous); }
  catch (error) { if (previous.restricted) throw error; previous.closed = true; return; }
  return previous;
 }
 if (!valid) return;
 const scope = Object.seal({owner, identity:Object.freeze({...identity}), current, origin:null, cap:null, restricted:false, closed:false, choice:null});
 odsRetryScopeTokens.add(scope);
 odsRetryScopes.set(owner, scope);
 odsAssertRetryToolScope(scope);
 return scope;
}
function odsAssertRetryToolScope(scope) {
 if (!scope || !odsRetryScopeTokens.has(scope) || scope.closed || scope.current() !== true ||
     scope.owner.sessionId !== scope.identity.sessionId || scope.owner.sessionKey !== scope.identity.sessionKey ||
     scope.owner.lifecycleGeneration !== scope.identity.lifecycleGeneration) {
  if (scope && odsRetryScopeTokens.has(scope) && scope.restricted) scope.closed = true;
  throw odsRetryScopeError();
 }
}
function odsRetryToolKey(tool, metadata) {
 const name = tool?.name;
 if (typeof name !== "string" || !/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(name)) return;
 const plugin = metadata?.(tool);
 if (plugin && (typeof plugin.pluginId !== "string" || !plugin.pluginId || plugin.pluginId.length > 128)) return;
 return JSON.stringify([plugin ? "plugin" : "core", plugin?.pluginId ?? null, name]);
}
function odsCaptureRetryToolOrigins(scope, tools, metadata) {
 if (!scope) return;
 try { odsAssertRetryToolScope(scope); }
 catch (error) { if (scope.restricted) throw error; scope.closed = true; return; }
 if (scope.origin !== null) return;
 if (!Array.isArray(tools) || tools.length > 2048) {
  if (scope.restricted) odsRejectRetryToolScope(scope);
  scope.closed = true; return;
 }
 const origin = new Map();
 for (const tool of tools) {
  const key = odsRetryToolKey(tool, metadata);
  if (!key) continue;
  origin.set(tool.name, origin.has(tool.name) ? null : key);
 }
 scope.origin = origin;
}
function odsAcceptRetryToolsAllow(scope, value) {
 try {
  odsAssertRetryToolScope(scope);
  // A present invalid request cannot become an unrestricted provider fallback.
  scope.restricted = true;
  const requested = odsNormalizeRetryToolsAllow(value);
  if (!scope.origin || requested.some(name => !scope.origin.get(name))) throw odsRetryScopeError();
  const next = odsMeetRetryToolsAllow([...(scope.cap === null ? [] : [scope.cap]), requested]);
  scope.cap = next;
  return next;
 } catch (error) {
  if (scope && odsRetryScopeTokens.has(scope)) { scope.restricted = true; scope.closed = true; }
  throw error;
 }
}
function odsAuthorizeRetryTool(scope, tool, metadata, current) {
 if (!scope || !scope.restricted && scope.cap === null) return;
 odsAssertRetryToolScope(scope);
 if (!scope.cap || typeof current !== "function" || current() !== true || !scope.cap.includes(tool?.name) ||
     scope.origin.get(tool.name) !== odsRetryToolKey(tool, metadata)) odsRejectRetryToolScope(scope);
}
function odsFilterRetryToolScope(scope, tools, metadata, copyMetadata, current) {
 if (!scope || !scope.restricted && scope.cap === null) return tools;
 odsAssertRetryToolScope(scope);
 if (!scope.cap) throw odsRetryScopeError();
 const matched = scope.cap.map(name => {
  const candidates = tools.filter(tool => tool.name === name && scope.origin.get(name) === odsRetryToolKey(tool, metadata));
  if (candidates.length !== 1) odsRejectRetryToolScope(scope);
  const original = candidates[0];
  const tool = {...original, execute:async (...args) => {
   odsAuthorizeRetryTool(scope, original, metadata, current);
   odsBeginRetryToolAttempt(scope, original, metadata, current);
   return await original.execute(...args);
  }};
  copyMetadata?.(original, tool);
  return tool;
 });
 return matched;
}
function odsRetryToolScopeVersion(scope) {
 try { odsAssertRetryToolScope(scope); return scope.origin !== null ? 1 : void 0; }
 catch { return; }
}
function odsRetryToolScopeState(scope, tools, metadata) {
 try {
  if (odsRetryToolScopeVersion(scope) !== 1) return;
  if (scope.cap === null) return Object.freeze({version:1, ownerRunId:scope.identity.runId, active:false, toolsAllow:null});
  if (!Array.isArray(tools) || tools.length !== scope.cap.length) odsRejectRetryToolScope(scope);
  const names = tools.map(tool => {
   odsAuthorizeRetryTool(scope, tool, metadata, () => true);
   return tool.name;
  });
  if (new Set(names).size !== names.length || scope.cap.some(name => !names.includes(name))) odsRejectRetryToolScope(scope);
  return Object.freeze({version:1, ownerRunId:scope.identity.runId, active:true, toolsAllow:Object.freeze([...names])});
 } catch { return; }
}
function odsTransferRetryToolDefinitions(scope, source, definitions, metadata, copyMetadata) {
 if (!scope || !scope.restricted && scope.cap === null) return;
 odsAssertRetryToolScope(scope);
 if (!Array.isArray(definitions) || definitions.length !== source.length || definitions.length !== scope.cap.length) odsRejectRetryToolScope(scope);
 for (let index = 0; index < definitions.length; index++) {
  const original = source[index], definition = definitions[index];
  odsAuthorizeRetryTool(scope, original, metadata, () => true);
  // The reviewed native adapter is a one-to-one tools.map projection. These
  // object/function references retain lineage; a matching name alone does not.
  if (!definition || definition.name !== original.name || definition.parameters !== original.parameters ||
      definition.prepareArguments !== original.prepareArguments || definition.executionMode !== original.executionMode ||
      definition.description !== (original.description ?? "") || typeof definition.execute !== "function") odsRejectRetryToolScope(scope);
  copyMetadata(original, definition);
 }
}
function odsCloseRetryToolScope(scope) { if (scope && odsRetryScopeTokens.has(scope)) scope.closed = true; }
// Required choice controls only one logical native provider invocation. It is
// not a tool grant, an argument selector or an additional execution path.
const odsRequiredRetryTools = Object.freeze(["pixel_ods_notes", "pixel_ods_mcp"]);
function odsAssertRequiredRetryCap(scope) {
 odsAssertRetryToolScope(scope);
 if (!scope.cap || scope.cap.length !== 2 || odsRequiredRetryTools.some(name =>
     !scope.cap.includes(name) || scope.origin?.get(name) !== JSON.stringify(["plugin", "pixel-ods", name]))) odsRejectRetryToolScope(scope);
}
function odsRetryToolChoiceVersion(scope, model, strategy) {
 try {
  if (odsRetryToolScopeVersion(scope) !== 1 || strategy !== "boundary-aware:openai-completions" ||
      model?.api !== "openai-completions" || model.provider !== "ods-gateway" || model.id !== "ods/current" || model.compat?.supportsTools === false || typeof model.baseUrl !== "string" || model.baseUrl.length > 2048) return;
  const url = new URL(model.baseUrl);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash || !["/v1", "/v1/"].includes(url.pathname)) return;
  return 1;
 } catch { return; }
}
function odsAcceptRetryToolChoice(scope, value) {
 try {
  odsAssertRequiredRetryCap(scope);
  if (value !== "required") odsRejectRetryToolScope(scope);
  if (scope.choice === null) scope.choice = "armed";
 } catch (error) { if (scope && odsRetryScopeTokens.has(scope)) { scope.restricted = true; scope.closed = true; } throw error; }
}
function odsRetryToolChoiceState(scope, supported) {
 try {
  if (supported !== 1 || scope.choice === null) return;
  odsAssertRequiredRetryCap(scope);
  if (!["armed", "tool-started"].includes(scope.choice)) return;
  return Object.freeze({version:1, ownerRunId:scope.identity.runId, mode:"required-first", phase:scope.choice});
 } catch { return; }
}
function odsBeginRetryToolAttempt(scope, tool, metadata, current) {
 if (!scope || scope.choice === null) return;
 odsAuthorizeRetryTool(scope, tool, metadata, current);
 odsAssertRequiredRetryCap(scope);
 if (scope.choice === "issued") scope.choice = "tool-started";
 else if (scope.choice !== "tool-started") odsRejectRetryToolScope(scope);
}
function odsWrapRetryToolChoiceStream(scope, underlying, supported, tools, metadata, routeModel) {
 if (supported !== 1) {
  if (scope?.choice !== null && scope?.choice !== void 0) odsRejectRetryToolScope(scope);
  return underlying;
 }
 const route = routeModel && Object.freeze(Object.fromEntries(["api", "provider", "id", "baseUrl"].map(key => [key, routeModel[key]])));
 const sameRoute = value => route && value && Object.keys(route).every(key => value[key] === route[key]) && value.compat?.supportsTools !== false;
 return (model, context, options) => {
  if (scope.choice === null) return underlying(model, context, options);
  odsAssertRequiredRetryCap(scope);
  const signal = options?.signal, explicitChoice = options?.toolChoice;
  if (signal?.aborted || !["armed", "tool-started"].includes(scope.choice) || !sameRoute(routeModel) || !sameRoute(model) ||
      odsRetryToolChoiceVersion(scope, model, "boundary-aware:openai-completions") !== 1) odsRejectRetryToolScope(scope);
  const proof = odsRetryToolScopeState(scope, tools, metadata);
  if (proof?.active !== true) odsRejectRetryToolScope(scope);
  const projection = values => values.map(tool => ({name:tool.name, description:tool.description ?? "", parameters:tool.parameters})).sort((a,b) => a.name.localeCompare(b.name));
  if (!Array.isArray(context?.tools) || JSON.stringify(projection(context.tools)) !== JSON.stringify(projection(tools))) odsRejectRetryToolScope(scope);
  const required = scope.choice === "armed";
  // Explicit native none/named restrictions must never be broadened by this
  // experiment. Unknown/incompatible choices stop instead of being replaced.
  if (explicitChoice !== void 0 && explicitChoice !== "auto" && !(required && explicitChoice === "required")) odsRejectRetryToolScope(scope);
  if (required) scope.choice = "issued"; // Synchronous reservation before any provider work.
  const prior = options?.onPayload;
  return underlying(model, context, {...options, signal, toolChoice:required ? "required" : "auto",
   onPayload:async (payload, callModel) => {
    odsAssertRequiredRetryCap(scope);
    if (signal?.aborted || !sameRoute(routeModel) || !sameRoute(model) || !sameRoute(callModel) || scope.choice !== (required ? "issued" : "tool-started")) odsRejectRetryToolScope(scope);
    const serializedTools = JSON.stringify(payload?.tools);
    const replacement = await prior?.(payload, callModel);
    // A callback may await owner code. Cancellation, native-context replacement
    // or terminal closure during that await must win before HTTP submission.
    odsAssertRequiredRetryCap(scope);
    if (signal?.aborted || !sameRoute(routeModel) || !sameRoute(model) || !sameRoute(callModel) ||
        odsRetryToolChoiceVersion(scope, model, "boundary-aware:openai-completions") !== 1 ||
        scope.choice !== (required ? "issued" : "tool-started")) odsRejectRetryToolScope(scope);
    const body = replacement === void 0 ? payload : replacement;
    // Native payload wrappers run their patches before this final callback.
    // A provider that drops the requirement or widens the menu cannot send it.
    if (!body || typeof body !== "object" || Array.isArray(body) || Object.hasOwn(body, "toJSON") || ["model", "tool_choice", "tools"].some(key => {
      const descriptor = Object.getOwnPropertyDescriptor(body, key); return !descriptor || !Object.hasOwn(descriptor, "value");
    })) odsRejectRetryToolScope(scope);
    if (JSON.stringify(body.tools) !== serializedTools || body.model !== route.id || body.tool_choice !== (required ? "required" : "auto") ||
        !Array.isArray(body.tools) || body.tools.length !== 2 ||
        new Set(body.tools.map(item => item?.function?.name)).size !== 2 ||
        body.tools.some(item => item?.type !== "function" || !odsRequiredRetryTools.includes(item.function?.name))) odsRejectRetryToolScope(scope);
    // Detach the accepted descriptors/schemas from any callback-owned aliases.
    return {...body, model:route.id, tool_choice:required ? "required" : "auto", tools:JSON.parse(serializedTools)};
   }});
 };
}
'''


def vectors():
    helper_exports = ", odsAcquireRetryToolScope, odsCaptureRetryToolOrigins, odsAcceptRetryToolsAllow, odsAuthorizeRetryTool, odsFilterRetryToolScope, odsRetryToolScopeVersion, odsRetryToolScopeState, odsTransferRetryToolDefinitions, odsRejectRetryToolScope, odsCloseRetryToolScope, odsRetryToolChoiceVersion, odsAcceptRetryToolChoice, odsRetryToolChoiceState, odsBeginRetryToolAttempt, odsWrapRetryToolChoiceStream"
    choice_normalize = '''
\t\tlet revisionToolChoiceRequired = false;
\t\ttry {
\t\t for (const retry of retryCandidates) if (Object.hasOwn(retry, "toolChoice")) {
\t\t  if (retry.toolChoice !== "required" || !Object.hasOwn(retry, "toolsAllow") || !revisionToolsAllow || revisionToolsAllow.length !== 2 || odsRequiredRetryTools.some(name => !revisionToolsAllow.includes(name))) throw odsRetryScopeError();
\t\t  revisionToolChoiceRequired = true;
\t\t }
\t\t} catch { return {action:"finalize", revisionToolsInvalid:true, reason:"The requested native revision tool choice is invalid. No broader retry was started."}; }'''
    return {
        "lifecycle-hook-helpers-Ck-WCTzd.js": {
            "sourceSha256": "ac3a1c2a03baace2a75de470f67d4c6337ad50c3d64fa9f69394be87bb8b3ec3",
            "replacements": [
                ["function normalizeBeforeAgentFinalizeResult(result, event) {", HELPER + "\nfunction normalizeBeforeAgentFinalizeResult(result, event) {"],
                ["\t\tconst retryCandidates = readBeforeAgentFinalizeRetryCandidates(result);", "\t\tconst retryCandidates = readBeforeAgentFinalizeRetryCandidates(result);\n\t\tlet revisionToolsAllow;\n\t\ttry { revisionToolsAllow = odsMeetRetryToolsAllow(retryCandidates.filter(retry => Object.hasOwn(retry, \"toolsAllow\")).map(retry => retry.toolsAllow)); }\n\t\tcatch { return {action:\"finalize\", revisionToolsInvalid:true, reason:\"The requested native revision tool scope is invalid. No broader retry was started.\"}; }" + choice_normalize],
                ['\t\t\t\t\taction: "revise",\n\t\t\t\t\treason: reason && reason.includes(retryInstruction)', '\t\t\t\t\taction: "revise",\n\t\t\t\t\t...(revisionToolsAllow !== void 0 ? {revisionToolsAllow} : {}),\n\t\t\t\t\t...(revisionToolChoiceRequired ? {revisionToolChoiceRequired:true} : {}),\n\t\t\t\t\treason: reason && reason.includes(retryInstruction)'],
                ['\t\t\treturn { action: "continue" };\n\t\t}\n\t\tconst reason = normalizeOptionalString(result.reason);', '\t\t\treturn { action: "continue", ...(revisionToolsAllow !== void 0 ? {revisionToolsAllow} : {}), ...(revisionToolChoiceRequired ? {revisionToolChoiceRequired:true} : {}) };\n\t\t}\n\t\tconst reason = normalizeOptionalString(result.reason);'],
                ['awaitAgentHarnessAgentEndHook as t };', 'awaitAgentHarnessAgentEndHook as t' + helper_exports + ' };'],
            ],
        },
        "embedded-agent-CJx-nG3W.js": {
            "sourceSha256": "d00804f960d617b6b3e2ae6b1691f3aa9866029c8da8772f236a35a6033b37f8",
            "replacements": [
                ['import { t as getGlobalHookRunner } from "./hook-runner-global-mWFYlTIy.js";', 'import { t as getGlobalHookRunner } from "./hook-runner-global-mWFYlTIy.js";\nimport { odsAcquireRetryToolScope, odsAcceptRetryToolsAllow, odsAcceptRetryToolChoice, odsCloseRetryToolScope } from "./lifecycle-hook-helpers-Ck-WCTzd.js";'],
                ['\t\tsessionFile: runSessionTarget.sessionFile\n\t};\n\tconst sessionLane =', '\t\tsessionFile: runSessionTarget.sessionFile\n\t};\n\tlet odsRetryToolScope;\n\tconst sessionLane ='],
                ['\t\t\treturn withAgentRunLifecycleGeneration(lifecycleGeneration, task);', '\t\t\tconst odsOwner = getAgentRunContext(params.runId);\n\t\t\todsRetryToolScope = odsAcquireRetryToolScope(odsOwner, {runId:params.runId,sessionId:params.sessionId,sessionKey:params.sessionKey,agentId:params.agentId,lifecycleGeneration}, () => getAgentRunContext(params.runId) === odsOwner && getAgentEventLifecycleGeneration() === lifecycleGeneration && params.abortSignal?.aborted !== true, existingContext);\n\t\t\treturn withAgentRunLifecycleGeneration(lifecycleGeneration, task);'],
                ['\t\t\t\t\t\ttoolsAllow: params.toolsAllow,', '\t\t\t\t\t\ttoolsAllow: params.toolsAllow,\n\t\t\t\t\t\todsRetryToolScope,'],
                ['\t\t\t\t\tif (beforeAgentFinalizeRevisionReason && shouldHonorBeforeAgentFinalizeRevision) {', '\t\t\t\t\tif (beforeAgentFinalizeRevisionReason && shouldHonorBeforeAgentFinalizeRevision) {\n\t\t\t\t\t\tif (attempt.beforeAgentFinalizeRevisionToolsAllow !== void 0) odsAcceptRetryToolsAllow(odsRetryToolScope, attempt.beforeAgentFinalizeRevisionToolsAllow);\n\t\t\t\t\t\tif (attempt.beforeAgentFinalizeRevisionToolChoice === true) odsAcceptRetryToolChoice(odsRetryToolScope, "required");'],
                ['\t\t\t\tif (params.isFinalFallbackAttempt !== false) await maybeEmitFastModeAutoResetBestEffort();', '\t\t\t\tif (params.isFinalFallbackAttempt !== false) { odsCloseRetryToolScope(odsRetryToolScope); await maybeEmitFastModeAutoResetBestEffort(); }'],
            ],
        },
        "selection-BEwSQKM-.js": {
            "sourceSha256": "07661b95137eceb301ad23bd4391c01ded07d3de0f7fb30741dde5a6c35df2e6",
            "replacements": [
                ['async function refreshSessionFileFence(beforeWrite) {\n\t\tif (takeoverDetected) return;\n\t\tconst snapshot = await readSessionFileFenceSnapshot(params.lockOptions.sessionFile);\n\t\tif (!sameSessionFileFingerprint(beforeWrite, snapshot.fingerprint) && fenceActive) {\n\t\t\tfenceFingerprint = snapshot.fingerprint;\n\t\t\tfenceSnapshot = snapshot;\n\t\t}\n\t}', 'async function refreshSessionFileFence(beforeWrite) {\n\t\tif (takeoverDetected) return;\n\t\tconst expectedGeneration = fenceGeneration;\n\t\tconst expectedFence = fenceFingerprint;\n\t\tconst snapshot = await readSessionFileFenceSnapshot(params.lockOptions.sessionFile);\n\t\tif (takeoverDetected || !fenceActive || fenceGeneration !== expectedGeneration || fenceFingerprint !== expectedFence) return;\n\t\tif (!sameSessionFileFingerprint(beforeWrite, snapshot.fingerprint)) {\n\t\t\tfenceFingerprint = snapshot.fingerprint;\n\t\t\tfenceSnapshot = snapshot;\n\t\t}\n\t}'],
                ['async function acquireWriteLock() {\n\t\tawait waitForHeldLockDrain();\n\t\tif (heldLock) return {', 'async function acquireWriteLock() {\n\t\tawait waitForHeldLockDrain();\n\t\tif (!heldLock && promptLockReacquisition) {\n\t\t\tawait promptLockReacquisition;\n\t\t\tawait waitForHeldLockDrain();\n\t\t}\n\t\tif (heldLock) return {'],
                ['import { i as getPluginToolMeta } from "./tools-D5HS8Q_I.js";', 'import { i as getPluginToolMeta, n as copyPluginToolMeta } from "./tools-D5HS8Q_I.js";'],
                ['import { i as runAgentHarnessBeforeAgentFinalizeHook } from "./lifecycle-hook-helpers-Ck-WCTzd.js";', 'import { i as runAgentHarnessBeforeAgentFinalizeHook, odsCaptureRetryToolOrigins, odsAcceptRetryToolsAllow, odsAuthorizeRetryTool, odsFilterRetryToolScope, odsRetryToolScopeVersion, odsRetryToolScopeState, odsTransferRetryToolDefinitions, odsRejectRetryToolScope, odsRetryToolChoiceVersion, odsAcceptRetryToolChoice, odsRetryToolChoiceState, odsWrapRetryToolChoiceStream } from "./lifecycle-hook-helpers-Ck-WCTzd.js";'],
                ['\t\tconst clientTools = toolsEnabled && !isRawModelRun ? params.clientTools : void 0;', '\t\tconst clientTools = params.odsRetryToolScope?.cap !== null && params.odsRetryToolScope?.cap !== void 0 ? void 0 : toolsEnabled && !isRawModelRun ? params.clientTools : void 0;'],
                ['\t\tconst uncompactedEffectiveTools = [...uncompactedToolSchemaProjection.tools];', '\t\todsCaptureRetryToolOrigins(params.odsRetryToolScope, uncompactedToolSchemaProjection.tools, getPluginToolMeta);\n\t\tconst uncompactedEffectiveTools = odsFilterRetryToolScope(params.odsRetryToolScope, [...uncompactedToolSchemaProjection.tools], getPluginToolMeta, copyPluginToolMeta, () => !runAbortController.signal.aborted && params.abortSignal?.aborted !== true);'],
                ['\t\tconst toolSearch = codeModeControlsEnabledForRun ? applyCodeModeCatalog({', '\t\tconst odsScopedRetry = params.odsRetryToolScope?.cap !== null && params.odsRetryToolScope?.cap !== void 0;\n\t\tif (odsScopedRetry) clearToolSearchCatalog({sessionId:params.sessionId,sessionKey:sandboxSessionKey,agentId:sessionAgentId,runId:params.runId,catalogRef:toolSearchCatalogRef});\n\t\tconst toolSearch = odsScopedRetry ? {tools:effectiveTools,compacted:false,catalogToolCount:0,catalogRegistered:false,catalogReused:false} : codeModeControlsEnabledForRun ? applyCodeModeCatalog({'],
                ['\t\t\tconst allCustomTools = [...customTools, ...clientToolDefs];', '\t\t\tconst allCustomTools = odsScopedRetry ? odsFilterRetryToolScope(params.odsRetryToolScope, [...customTools, ...clientToolDefs], getPluginToolMeta, copyPluginToolMeta, () => !runAbortController.signal.aborted && params.abortSignal?.aborted !== true) : [...customTools, ...clientToolDefs];'],
                ['\t\t\tconst clientToolCallSlots = [];', '\t\t\todsTransferRetryToolDefinitions(params.odsRetryToolScope, effectiveTools, customTools, getPluginToolMeta, copyPluginToolMeta);\n\t\t\tconst clientToolCallSlots = [];'],
                ['\t\t\tlet diagnosticModelCallSeq = 0;', '\t\t\tconst odsNativeRetryToolChoiceSupport = odsRetryToolChoiceVersion(params.odsRetryToolScope, params.model, streamStrategy);\n\t\t\tactiveSession.agent.streamFn = odsWrapRetryToolChoiceStream(params.odsRetryToolScope, activeSession.agent.streamFn, odsNativeRetryToolChoiceSupport, allCustomTools, getPluginToolMeta, params.model);\n\t\t\tlet diagnosticModelCallSeq = 0;'],
                ['\t\t\tlet beforeAgentFinalizeRevisionReason;', '\t\t\tlet beforeAgentFinalizeRevisionReason;\n\t\t\tlet beforeAgentFinalizeRevisionToolsAllow;\n\t\t\tlet beforeAgentFinalizeRevisionToolChoice;'],
                ['\t\t\t\t\t\tstopHookActive: false,', '\t\t\t\t\t\tstopHookActive: false,\n\t\t\t\t\t\t...(odsRetryToolScopeVersion(params.odsRetryToolScope) === 1 ? {nativeRetryToolsAllowVersion:1} : {}),\n\t\t\t\t\t\t...(odsNativeRetryToolChoiceSupport === 1 && odsRetryToolChoiceVersion(params.odsRetryToolScope, params.model, streamStrategy) === 1 ? {nativeRetryToolChoiceVersion:1} : {}),'],
                ['\t\t\t\tif (outcome.action !== "revise") return;', '\t\t\t\tif (outcome.revisionToolsInvalid === true) odsRejectRetryToolScope(params.odsRetryToolScope);\n\t\t\t\tif (outcome.revisionToolsAllow !== void 0) beforeAgentFinalizeRevisionToolsAllow = odsAcceptRetryToolsAllow(params.odsRetryToolScope, outcome.revisionToolsAllow);\n\t\t\t\tif (outcome.revisionToolChoiceRequired === true) {\n\t\t\t\t if (odsNativeRetryToolChoiceSupport !== 1) odsRejectRetryToolScope(params.odsRetryToolScope);\n\t\t\t\t odsAcceptRetryToolChoice(params.odsRetryToolScope, "required"); beforeAgentFinalizeRevisionToolChoice = true;\n\t\t\t\t}\n\t\t\t\tif (outcome.action !== "revise") return;'],
                ['\t\t\t\t\t\t\tbeforeRunResult = await hookRunner.runBeforeAgentRun({', '\t\t\t\t\t\t\tbeforeRunResult = await hookRunner.runBeforeAgentRun({\n\t\t\t\t\t\t\t\tnativeRetryToolsAllowState: odsRetryToolScopeState(params.odsRetryToolScope, allCustomTools, getPluginToolMeta),\n\t\t\t\t\t\t\t\tnativeRetryToolChoiceState: odsRetryToolChoiceState(params.odsRetryToolScope, odsNativeRetryToolChoiceSupport),'],
                ['\t\t\ttoolSearchCatalogExecutor = async (toolParams) => {\n\t\t\t\ttry {', '\t\t\ttoolSearchCatalogExecutor = async (toolParams) => {\n\t\t\t\ttry {\n\t\t\t\t\todsAuthorizeRetryTool(params.odsRetryToolScope, toolParams.tool, getPluginToolMeta, () => !runAbortController.signal.aborted && params.abortSignal?.aborted !== true);'],
                ['\t\t\t\t...beforeAgentFinalizeRevisionReason ? { beforeAgentFinalizeRevisionReason } : {},', '\t\t\t\t...beforeAgentFinalizeRevisionReason ? { beforeAgentFinalizeRevisionReason, ...(beforeAgentFinalizeRevisionToolsAllow !== void 0 ? {beforeAgentFinalizeRevisionToolsAllow} : {}), ...(beforeAgentFinalizeRevisionToolChoice === true ? {beforeAgentFinalizeRevisionToolChoice:true} : {}) } : {},'],
            ],
        },
    }


def apply(sdk, output):
    result = {"version": "2026.6.33", "scope": "added-vectors-over-reviewed-ODS-SDK", "modules": vectors(),
              "reviewedReadDependencies": READ_DEPENDENCIES}
    for name, digest in READ_DEPENDENCIES.items():
        if hashlib.sha256((sdk / "dist" / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Private SDK canonical read dependency differs: " + name)
    prepared = {}
    for name, contract in result["modules"].items():
        path = sdk / "dist" / name
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != contract["sourceSha256"]:
            raise ValueError("Private SDK source differs: " + name)
        candidate = source.decode("utf-8")
        for before, after in contract["replacements"]:
            if candidate.count(before) != 1:
                raise ValueError("Nonunique source anchor in " + name + ": " + before[:100])
            candidate = candidate.replace(before, after)
        body = candidate.encode("utf-8")
        contract["patchedSha256"] = hashlib.sha256(body).hexdigest()
        prepared[path] = body
    for path, body in prepared.items():
        path.write_bytes(body)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sdk": str(sdk), "vectors": str(output), "hashes": {name: item["patchedSha256"] for name, item in result["modules"].items()}}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    apply(arguments.sdk, arguments.output)
