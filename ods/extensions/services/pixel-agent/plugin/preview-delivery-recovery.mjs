const TOOL = 'pixel_ods_workspace_preview';

// Automatic delivery is deliberately narrower than the model's tool policy.
// Unknown/custom restrictions fall back to ordinary, policy-filtered tools.
export function previewRecoveryAllowed(config, agentId = 'pixel') {
  const agent = config?.agents?.list?.find(item => item.id === agentId);
  if (!agent) return false;
  const sandboxMode = agent.sandbox?.mode ?? config?.agents?.defaults?.sandbox?.mode ?? 'off';
  const sandboxTools = agent.tools?.sandbox?.tools ?? config?.tools?.sandbox?.tools;
  if (sandboxMode !== 'off' && !sandboxTools?.allow?.includes(TOOL)) return false;
  const scopes = [config?.tools, agent.tools, config?.tools?.sandbox?.tools,
    agent.tools?.sandbox?.tools];
  if (!scopes.some(scope => [...(scope?.allow ?? []), ...(scope?.alsoAllow ?? [])].includes(TOOL))) return false;
  return scopes.every(scope => !scope || (
    !scope.byProvider && (scope.deny ?? []).every(name =>
      /^[a-z0-9_]+$/.test(name) && name !== TOOL) &&
    (!scope.profile || ['coding', 'full'].includes(scope.profile)) &&
    (!scope.allow || scope.allow.includes(TOOL))
  ));
}

export async function boundedPreviewDelivery(publish, params, valid, {timeoutMs = 30_000} = {}) {
  const controller = new AbortController();
  let timer, poll;
  try {
    if (!valid()) return undefined;
    const stopped = new Promise(resolve => {
      const stop = () => { controller.abort(); resolve(undefined); };
      timer = setTimeout(stop, timeoutMs);
      poll = setInterval(() => { if (!valid()) stop(); }, 25);
    });
    const result = await Promise.race([stopped, Promise.resolve().then(() =>
      valid() ? publish(params, {signal: controller.signal}) : undefined)]);
    return !controller.signal.aborted && valid() ? result : undefined;
  } catch { return undefined; }
  finally { clearTimeout(timer); clearInterval(poll); controller.abort(); }
}
