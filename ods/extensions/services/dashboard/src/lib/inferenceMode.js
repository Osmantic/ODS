// Pure helper: derive the active inference mode/source from a status payload.
// Never trusts local GPU telemetry when the backend reports remote/cloud
// inference. Returns { mode, source, isRemote, isCloud, isLocal }.
export function getInferenceMode(status) {
  const mode = typeof status?.inferenceMode === 'string' ? status.inferenceMode : null
  const source = typeof status?.inferenceSource === 'string' ? status.inferenceSource : null
  const isRemote = mode === 'remote' || source === 'remote-provider'
  const isCloud = mode === 'cloud' || source === 'cloud-mode'
  const isLocal = !isRemote && !isCloud
  return {
    mode: isRemote ? 'remote' : isCloud ? 'cloud' : 'local',
    source: source || (isLocal ? 'local-runtime' : 'unknown'),
    isRemote,
    isCloud,
    isLocal,
  }
}

export function isRemoteInference(status) {
  const { isRemote, isCloud } = getInferenceMode(status)
  return isRemote || isCloud
}
