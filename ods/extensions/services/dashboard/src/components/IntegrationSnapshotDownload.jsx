export default function IntegrationSnapshotDownload({ nodes, edges, capturedAt, refreshFailed }) {
  if (!capturedAt || !nodes.length) return null
  const receipt = {
    schemaVersion: 1,
    capturedAt,
    refreshFailed,
    services: nodes.map(({ id, name, status, port, category }) => ({ id, name, status, port, category })),
    knownDependencies: edges,
  }
  return <a
    className="text-xs text-theme-accent hover:underline"
    download="ods-integrations.json"
    href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(receipt, null, 2))}`}
    title="Download all reported services and known dependencies from the last successful refresh"
  >Download snapshot</a>
}
