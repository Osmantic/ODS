import { routeWorkProvider } from "../../deploy/work-provider-router/router.mjs";
import { resolveWorkProvider } from "../../deploy/work-provider/provider-registry.mjs";
import { defaultWorkProviderLocalPolicy } from "../../deploy/work-provider/local-policy.mjs";
import { makeLocalQualification, makePrivatePolicy, makeQualification, makeRequest, makeRouterPolicy } from "./work-provider-router.mjs";

export const NOW = new Date("2026-08-21T12:00:00Z");
export const SUFFIX = "abcdef123456";

export function semanticSetup(providerId) {
  if (providerId === "local") {
    const resolvedProvider = resolveWorkProvider("local");
    const policy = defaultWorkProviderLocalPolicy(resolvedProvider);
    const routerPolicy = makeRouterPolicy();
    const qualification = makeLocalQualification();
    const qualifications = { local: qualification };
    const request = makeRequest({ mode: "local-only" });
    const { decision } = routeWorkProvider({ request, routerPolicy, enabledPrivatePolicies: undefined, qualifications, now: NOW, suffix: SUFFIX });
    return { resolvedProvider, policy, routerPolicy, qualification, qualifications, enabledPrivatePolicies: undefined, request, decision, privatePolicy: null, model: "DeepSeek-V4-Flash-0731" };
  }
  const resolvedProvider = resolveWorkProvider(providerId, { enabledRemoteProviders: [providerId] });
  const privatePolicy = makePrivatePolicy(providerId);
  const routerPolicy = makeRouterPolicy({ providerId });
  const qualification = makeQualification(providerId);
  const request = makeRequest({ mode: "explicit-provider", explicitProviderId: providerId });
  const enabledPrivatePolicies = { [providerId]: privatePolicy };
  const qualifications = { local: makeLocalQualification(), [providerId]: qualification };
  const { decision } = routeWorkProvider({
    request, routerPolicy,
    enabledPrivatePolicies,
    qualifications,
    now: NOW, suffix: SUFFIX,
  });
  return { resolvedProvider, policy: privatePolicy, routerPolicy, qualification, qualifications, enabledPrivatePolicies, request, decision, privatePolicy, model: "kimi-k3" };
}
