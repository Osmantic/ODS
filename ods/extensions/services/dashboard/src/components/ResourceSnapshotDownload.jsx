import { useMemo } from 'react'

export default function ResourceSnapshotDownload({ snapshot, refreshFailed }) {
  const href = useMemo(() => {
    if (!snapshot?.data.services.length) return null
    const { data, receivedAt } = snapshot
    const receipt = {
      schemaVersion: 1,
      source: '/api/services/resources',
      receivedAt,
      refreshFailed,
      notes: 'Received time is a browser receipt, not a measurement timestamp. Container and disk metrics may use separate server caches.',
      units: { cpu: 'percent (may exceed 100)', memory: 'MB', disk: 'GB' },
      services: data.services.map(({ id, name, type, container, disk }) => ({
        id, name, type,
        container: container ? {
          container_name: container.container_name,
          cpu_percent: container.cpu_percent,
          memory_used_mb: container.memory_used_mb,
          memory_limit_mb: container.memory_limit_mb,
          memory_percent: container.memory_percent,
          pids: container.pids,
        } : null,
        disk: disk ? { data_gb: disk.data_gb, path: disk.path } : null,
      })),
      totals: {
        cpu_percent: data.totals?.cpu_percent ?? null,
        memory_used_mb: data.totals?.memory_used_mb ?? null,
        disk_data_gb: data.totals?.disk_data_gb ?? null,
      },
      caveats: { docker_desktop_memory: data.caveats?.docker_desktop_memory ?? null },
    }
    return `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(receipt, null, 2))}`
  }, [snapshot, refreshFailed])

  if (!href) return null
  return <div className="text-xs text-theme-text-muted">
    <a className="text-theme-accent hover:underline" download="ods-resources.json" href={href}
      title="Save the last received ODS service CPU, memory and disk metrics as JSON">Export resources</a>
    {refreshFailed && <p role="status">Last refresh failed; export uses the last successful snapshot.</p>}
  </div>
}
