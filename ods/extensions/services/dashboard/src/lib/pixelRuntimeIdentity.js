// Version 1 is partial diagnostics, never full release verification. Read only
// allowlisted values, including on rolling upgrades with missing/older APIs.
export function readRuntimeIdentity(value) {
  const identities = value?.identities, schemas = value?.toolSchemas
  const hash = item => item === null || typeof item === 'string' && /^[a-f0-9]{64}$/.test(item)
  const reasons = {partial:'release-binding-unavailable', mismatch:'runtime-files-changed', unavailable:'runtime-identity-unavailable'}
  if (!value || value.schemaVersion !== 1 || !Object.hasOwn(reasons, value.state)
    || value.boundary !== 'initialization-files-not-evaluated-code-or-release-proof'
    || value.reasonCode !== reasons[value.state]
    || !['match', 'mismatch', 'unavailable'].includes(value.diskComparison)
    || (value.state === 'mismatch') !== (value.diskComparison === 'mismatch')
    || value.runtimeMatchesRelease !== (value.state === 'mismatch' ? false : null)
    || typeof value.observedAt !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value.observedAt)
    || !Number.isFinite(Date.parse(value.observedAt))
    || !identities || identities.odsReleaseCommit !== null || identities.pixelSourceRevision !== null || identities.previewImageDigest !== null
    || !hash(identities.pluginSha256) || !hash(identities.openclawModuleSha256)
    || !(identities.openclawVersion === null || typeof identities.openclawVersion === 'string' && /^[0-9]{4}\.[0-9]+\.[0-9]+(?:-[0-9]+)?$/.test(identities.openclawVersion))
    || !schemas || schemas.boundary !== 'latest-created-plugin-tools-not-offered-surface'
    || !Number.isInteger(schemas.registeredPluginToolCount) || schemas.registeredPluginToolCount < 0 || schemas.registeredPluginToolCount > 64
    || !hash(schemas.registeredPluginToolSchemasSha256)
    || (schemas.registeredPluginToolCount === 0) !== (schemas.registeredPluginToolSchemasSha256 === null)
    || schemas.offeredToolCount !== null || schemas.offeredToolSchemasSha256 !== null) return null
  return {
    runtimeMatchesRelease: value.runtimeMatchesRelease, diskComparison: value.diskComparison, observedAt: value.observedAt,
    identities: {pluginSha256:identities.pluginSha256, openclawModuleSha256:identities.openclawModuleSha256, openclawVersion:identities.openclawVersion},
    toolSchemas: {registeredPluginToolCount:schemas.registeredPluginToolCount, registeredPluginToolSchemasSha256:schemas.registeredPluginToolSchemasSha256},
  }
}
