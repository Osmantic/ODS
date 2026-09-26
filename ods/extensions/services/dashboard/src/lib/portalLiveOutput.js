// Measured output of the local runtime's sole active generation. The status
// sample observes only the local model route: remote and external routes,
// stale samples and idle or shared runtimes have no live count to show.
export function liveModelOutputTokens(runtime, systemStatus) {
  const inference = systemStatus?.inference
  const count = inference?.liveOutputTokens
  return runtime?.source === 'local-switchboard' && inference?.inferenceActive === true
    && systemStatus?.clientTelemetry?.stale !== true && Number.isSafeInteger(count) && count > 0
    ? count : null
}
