import {readRuntimeIdentity} from '../lib/pixelRuntimeIdentity'

export default function PortalRuntimeIdentity({identity, runtime}) {
  const value = readRuntimeIdentity(identity)
  const mismatch = value?.runtimeMatchesRelease === false
  const rows = [
    ['ODS release commit', null],
    ['Pixel source revision', null],
    ['Plugin initialization SHA-256', value?.identities.pluginSha256],
    ['OpenClaw tool-search initialization SHA-256', value?.identities.openclawModuleSha256],
    ['OpenClaw reported version', value?.identities.openclawVersion],
    ['Preview image digest', null],
    ['Created plugin-tool schema SHA-256', value?.toolSchemas.registeredPluginToolSchemasSha256],
    ['Created plugin tools (not offered tools)', value?.toolSchemas.registeredPluginToolCount],
    ['Model-facing offered schema SHA-256', null],
    ['Model', runtime?.model],
    ['Model context tokens', runtime?.contextLength],
  ]
  return <details className="p-2 text-xs" data-testid="pixel-runtime-identity">
    <summary>Runtime identity · {mismatch ? 'Files changed' : 'Unverified'}</summary>
    <p className="mt-2">{mismatch ? 'Installed files changed after this process initialized. ' : ''}Chat availability does not verify the installed release. Initialization file hashes do not prove the JavaScript evaluated by the running process.</p>
    <p className="mt-2">Release, Pixel source, preview image and final model-facing schema bindings are not yet available. Created plugin schemas are observations before native tool filtering.</p>
    <dl className="mt-2 space-y-2">{rows.map(([label, item]) => <div key={label}>
      <dt className="text-theme-text-secondary">{label}</dt>
      <dd className="break-all font-mono">{item ?? 'Unknown'}</dd>
    </div>)}</dl>
    <p className="mt-2">Disk comparison: {value?.diskComparison || 'unavailable'}. Observed: {value?.observedAt || 'Unknown'}.</p>
  </details>
}
