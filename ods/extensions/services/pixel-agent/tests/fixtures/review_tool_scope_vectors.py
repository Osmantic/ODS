"""Reviewed added vectors against the already qualified ODS 2026.6.33 SDK.

This development fixture writes only an explicitly selected private SDK copy.
Production repair recipes and their original-source custody remain separate.
"""
import argparse
import hashlib
import json
from pathlib import Path


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
 const scope = Object.seal({owner, identity:Object.freeze({...identity}), current, origin:null, cap:null, restricted:false, closed:false});
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
'''


def vectors():
    helper_exports = ", odsAcquireRetryToolScope, odsCaptureRetryToolOrigins, odsAcceptRetryToolsAllow, odsAuthorizeRetryTool, odsFilterRetryToolScope, odsRetryToolScopeVersion, odsTransferRetryToolDefinitions, odsRejectRetryToolScope, odsCloseRetryToolScope"
    return {
        "lifecycle-hook-helpers-Ck-WCTzd.js": {
            "sourceSha256": "ac3a1c2a03baace2a75de470f67d4c6337ad50c3d64fa9f69394be87bb8b3ec3",
            "replacements": [
                ["function normalizeBeforeAgentFinalizeResult(result, event) {", HELPER + "\nfunction normalizeBeforeAgentFinalizeResult(result, event) {"],
                ["\t\tconst retryCandidates = readBeforeAgentFinalizeRetryCandidates(result);", "\t\tconst retryCandidates = readBeforeAgentFinalizeRetryCandidates(result);\n\t\tlet revisionToolsAllow;\n\t\ttry { revisionToolsAllow = odsMeetRetryToolsAllow(retryCandidates.filter(retry => Object.hasOwn(retry, \"toolsAllow\")).map(retry => retry.toolsAllow)); }\n\t\tcatch { return {action:\"finalize\", revisionToolsInvalid:true, reason:\"The requested native revision tool scope is invalid. No broader retry was started.\"}; }"],
                ['\t\t\t\t\taction: "revise",\n\t\t\t\t\treason: reason && reason.includes(retryInstruction)', '\t\t\t\t\taction: "revise",\n\t\t\t\t\t...(revisionToolsAllow !== void 0 ? {revisionToolsAllow} : {}),\n\t\t\t\t\treason: reason && reason.includes(retryInstruction)'],
                ['\t\t\treturn { action: "continue" };\n\t\t}\n\t\tconst reason = normalizeOptionalString(result.reason);', '\t\t\treturn { action: "continue", ...(revisionToolsAllow !== void 0 ? {revisionToolsAllow} : {}) };\n\t\t}\n\t\tconst reason = normalizeOptionalString(result.reason);'],
                ['awaitAgentHarnessAgentEndHook as t };', 'awaitAgentHarnessAgentEndHook as t' + helper_exports + ' };'],
            ],
        },
        "embedded-agent-CJx-nG3W.js": {
            "sourceSha256": "d00804f960d617b6b3e2ae6b1691f3aa9866029c8da8772f236a35a6033b37f8",
            "replacements": [
                ['import { t as getGlobalHookRunner } from "./hook-runner-global-mWFYlTIy.js";', 'import { t as getGlobalHookRunner } from "./hook-runner-global-mWFYlTIy.js";\nimport { odsAcquireRetryToolScope, odsAcceptRetryToolsAllow, odsCloseRetryToolScope } from "./lifecycle-hook-helpers-Ck-WCTzd.js";'],
                ['\t\tsessionFile: runSessionTarget.sessionFile\n\t};\n\tconst sessionLane =', '\t\tsessionFile: runSessionTarget.sessionFile\n\t};\n\tlet odsRetryToolScope;\n\tconst sessionLane ='],
                ['\t\t\treturn withAgentRunLifecycleGeneration(lifecycleGeneration, task);', '\t\t\tconst odsOwner = getAgentRunContext(params.runId);\n\t\t\todsRetryToolScope = odsAcquireRetryToolScope(odsOwner, {runId:params.runId,sessionId:params.sessionId,sessionKey:params.sessionKey,agentId:params.agentId,lifecycleGeneration}, () => getAgentRunContext(params.runId) === odsOwner && getAgentEventLifecycleGeneration() === lifecycleGeneration && params.abortSignal?.aborted !== true, existingContext);\n\t\t\treturn withAgentRunLifecycleGeneration(lifecycleGeneration, task);'],
                ['\t\t\t\t\t\ttoolsAllow: params.toolsAllow,', '\t\t\t\t\t\ttoolsAllow: params.toolsAllow,\n\t\t\t\t\t\todsRetryToolScope,'],
                ['\t\t\t\t\tif (beforeAgentFinalizeRevisionReason && shouldHonorBeforeAgentFinalizeRevision) {', '\t\t\t\t\tif (beforeAgentFinalizeRevisionReason && shouldHonorBeforeAgentFinalizeRevision) {\n\t\t\t\t\t\tif (attempt.beforeAgentFinalizeRevisionToolsAllow !== void 0) odsAcceptRetryToolsAllow(odsRetryToolScope, attempt.beforeAgentFinalizeRevisionToolsAllow);'],
                ['\t\t\t\tif (params.isFinalFallbackAttempt !== false) await maybeEmitFastModeAutoResetBestEffort();', '\t\t\t\tif (params.isFinalFallbackAttempt !== false) { odsCloseRetryToolScope(odsRetryToolScope); await maybeEmitFastModeAutoResetBestEffort(); }'],
            ],
        },
        "selection-BEwSQKM-.js": {
            "sourceSha256": "07661b95137eceb301ad23bd4391c01ded07d3de0f7fb30741dde5a6c35df2e6",
            "replacements": [
                ['import { i as getPluginToolMeta } from "./tools-D5HS8Q_I.js";', 'import { i as getPluginToolMeta, n as copyPluginToolMeta } from "./tools-D5HS8Q_I.js";'],
                ['import { i as runAgentHarnessBeforeAgentFinalizeHook } from "./lifecycle-hook-helpers-Ck-WCTzd.js";', 'import { i as runAgentHarnessBeforeAgentFinalizeHook, odsCaptureRetryToolOrigins, odsAcceptRetryToolsAllow, odsAuthorizeRetryTool, odsFilterRetryToolScope, odsRetryToolScopeVersion, odsTransferRetryToolDefinitions, odsRejectRetryToolScope } from "./lifecycle-hook-helpers-Ck-WCTzd.js";'],
                ['\t\tconst clientTools = toolsEnabled && !isRawModelRun ? params.clientTools : void 0;', '\t\tconst clientTools = params.odsRetryToolScope?.cap !== null && params.odsRetryToolScope?.cap !== void 0 ? void 0 : toolsEnabled && !isRawModelRun ? params.clientTools : void 0;'],
                ['\t\tconst uncompactedEffectiveTools = [...uncompactedToolSchemaProjection.tools];', '\t\todsCaptureRetryToolOrigins(params.odsRetryToolScope, uncompactedToolSchemaProjection.tools, getPluginToolMeta);\n\t\tconst uncompactedEffectiveTools = odsFilterRetryToolScope(params.odsRetryToolScope, [...uncompactedToolSchemaProjection.tools], getPluginToolMeta, copyPluginToolMeta, () => !runAbortController.signal.aborted && params.abortSignal?.aborted !== true);'],
                ['\t\tconst toolSearch = codeModeControlsEnabledForRun ? applyCodeModeCatalog({', '\t\tconst odsScopedRetry = params.odsRetryToolScope?.cap !== null && params.odsRetryToolScope?.cap !== void 0;\n\t\tif (odsScopedRetry) clearToolSearchCatalog({sessionId:params.sessionId,sessionKey:sandboxSessionKey,agentId:sessionAgentId,runId:params.runId,catalogRef:toolSearchCatalogRef});\n\t\tconst toolSearch = odsScopedRetry ? {tools:effectiveTools,compacted:false,catalogToolCount:0,catalogRegistered:false,catalogReused:false} : codeModeControlsEnabledForRun ? applyCodeModeCatalog({'],
                ['\t\t\tconst allCustomTools = [...customTools, ...clientToolDefs];', '\t\t\tconst allCustomTools = odsScopedRetry ? odsFilterRetryToolScope(params.odsRetryToolScope, [...customTools, ...clientToolDefs], getPluginToolMeta, copyPluginToolMeta, () => !runAbortController.signal.aborted && params.abortSignal?.aborted !== true) : [...customTools, ...clientToolDefs];'],
                ['\t\t\tconst clientToolCallSlots = [];', '\t\t\todsTransferRetryToolDefinitions(params.odsRetryToolScope, effectiveTools, customTools, getPluginToolMeta, copyPluginToolMeta);\n\t\t\tconst clientToolCallSlots = [];'],
                ['\t\t\tlet beforeAgentFinalizeRevisionReason;', '\t\t\tlet beforeAgentFinalizeRevisionReason;\n\t\t\tlet beforeAgentFinalizeRevisionToolsAllow;'],
                ['\t\t\t\t\t\tstopHookActive: false,', '\t\t\t\t\t\tstopHookActive: false,\n\t\t\t\t\t\t...(odsRetryToolScopeVersion(params.odsRetryToolScope) === 1 ? {nativeRetryToolsAllowVersion:1} : {}),'],
                ['\t\t\t\tif (outcome.action !== "revise") return;', '\t\t\t\tif (outcome.revisionToolsInvalid === true) odsRejectRetryToolScope(params.odsRetryToolScope);\n\t\t\t\tif (outcome.revisionToolsAllow !== void 0) beforeAgentFinalizeRevisionToolsAllow = odsAcceptRetryToolsAllow(params.odsRetryToolScope, outcome.revisionToolsAllow);\n\t\t\t\tif (outcome.action !== "revise") return;'],
                ['\t\t\ttoolSearchCatalogExecutor = async (toolParams) => {\n\t\t\t\ttry {', '\t\t\ttoolSearchCatalogExecutor = async (toolParams) => {\n\t\t\t\ttry {\n\t\t\t\t\todsAuthorizeRetryTool(params.odsRetryToolScope, toolParams.tool, getPluginToolMeta, () => !runAbortController.signal.aborted && params.abortSignal?.aborted !== true);'],
                ['\t\t\t\t...beforeAgentFinalizeRevisionReason ? { beforeAgentFinalizeRevisionReason } : {},', '\t\t\t\t...beforeAgentFinalizeRevisionReason ? { beforeAgentFinalizeRevisionReason, ...(beforeAgentFinalizeRevisionToolsAllow !== void 0 ? {beforeAgentFinalizeRevisionToolsAllow} : {}) } : {},'],
            ],
        },
    }


def apply(sdk, output):
    result = {"version": "2026.6.33", "scope": "added-vectors-over-reviewed-ODS-SDK", "modules": vectors()}
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
