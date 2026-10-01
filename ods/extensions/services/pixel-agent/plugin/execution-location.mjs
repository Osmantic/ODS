// Execution location and model routing are different facts. A loopback ODS
// gateway can forward to cloud inference, so host/tool metadata is insufficient.
export const EXECUTION_LOCATION_CONTRACT = 'Describe execution and inference separately. Running Python, shell commands, or containers on the ODS host does not prove that the selected model is local. A localhost or ods/current gateway can route to a remote provider. Do not claim that no remote model calls occurred, or report local/cloud token percentages, without current route and usage evidence. If that evidence is unavailable, leave inference location unverified. Use the selected ODS model and configured tools; do not invent additional machines, worker assignments, or mandatory local-inference quotas. Fleet operations require an actual owner request and configured capabilities; an inherited example is not evidence of this installation.';

export function executionLocationContext(context, agentId = 'pixel') {
  return context?.agentId === agentId ? EXECUTION_LOCATION_CONTRACT : '';
}
