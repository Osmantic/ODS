import { memo } from 'react'
import { Network } from 'lucide-react'

// Rank → visual style mapping
function linkStyle(rank) {
  if (rank >= 100) return { bg: 'bg-green-500/20', text: 'text-green-400', dot: 'bg-green-400' }
  if (rank >= 60)  return { bg: 'bg-indigo-500/20', text: 'text-indigo-400', dot: 'bg-indigo-400' }
  if (rank >= 40)  return { bg: 'bg-yellow-500/20', text: 'text-yellow-400', dot: 'bg-yellow-400' }
  if (rank >= 20)  return { bg: 'bg-orange-500/20', text: 'text-orange-400', dot: 'bg-orange-400' }
  return           { bg: 'bg-red-500/20', text: 'text-red-400', dot: 'bg-red-400' }
}

function buildMatrix(gpuCount, links) {
  // matrix[a][b] = link object or null
  const m = Array.from({ length: gpuCount }, () => Array(gpuCount).fill(null))
  for (const link of links) {
    m[link.gpu_a][link.gpu_b] = link
    m[link.gpu_b][link.gpu_a] = link
  }
  return m
}

export const TopologyView = memo(function TopologyView({ topology }) {
  if (!topology) return null

  const { gpus = [], links = [], gpu_count, vendor, driver_version, mig_enabled } = topology
  const n = gpu_count || gpus.length
  const matrix = buildMatrix(n, links)

  return (
    <div className="p-5 bg-zinc-900/50 border border-zinc-800 rounded-xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Network size={16} className="text-indigo-400" />
          <h3 className="text-sm font-semibold text-white">GPU Interconnect Topology</h3>
        </div>
        <div className="flex items-center gap-3 text-[10px] font-mono text-zinc-500">
          {driver_version && <span>driver {driver_version}</span>}
          {mig_enabled && (
            <span className="px-1.5 py-0.5 bg-purple-500/15 text-purple-400 rounded">MIG</span>
          )}
          <span className="uppercase">{vendor}</span>
        </div>
      </div>

      {/* GPU reference chips */}
      <div className="flex flex-wrap gap-2 mb-4">
        {gpus.map(g => (
          <div key={g.index} className="flex items-center gap-1.5 px-2 py-1 bg-zinc-800 rounded-lg text-xs">
            <span className="text-indigo-300 font-mono">GPU{g.index}</span>
            <span className="text-zinc-400">{g.name.replace('NVIDIA ', '').replace('AMD Radeon ', '')}</span>
            <span className="text-zinc-600 font-mono">{g.memory_gb}GB</span>
          </div>
        ))}
      </div>

      {/* Topology matrix table */}
      {n > 1 && links.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-auto border-collapse text-xs font-mono">
            <thead>
              <tr>
                <th className="px-2 py-1.5 text-zinc-500 text-left" />
                {Array.from({ length: n }, (_, i) => (
                  <th key={i} className="px-3 py-1.5 text-indigo-300 text-center font-medium">
                    GPU{i}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: n }, (_, row) => (
                <tr key={row}>
                  <td className="px-2 py-1.5 text-indigo-300 font-medium whitespace-nowrap">
                    GPU{row}
                  </td>
                  {Array.from({ length: n }, (_, col) => {
                    if (row === col) {
                      return (
                        <td key={col} className="px-3 py-1.5 text-center">
                          <span className="text-zinc-600">—</span>
                        </td>
                      )
                    }
                    const link = matrix[row][col]
                    if (!link) {
                      return (
                        <td key={col} className="px-3 py-1.5 text-center text-zinc-600">?</td>
                      )
                    }
                    const style = linkStyle(link.rank || 0)
                    return (
                      <td key={col} className="px-1 py-1">
                        <span
                          className={`block px-2 py-1 rounded text-center text-[10px] ${style.bg} ${style.text}`}
                          title={`GPU${link.gpu_a} ↔ GPU${link.gpu_b}: ${link.link_type}`}
                        >
                          {link.link_type}
                        </span>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : n <= 1 ? (
        <p className="text-xs text-zinc-500 text-center py-2">Single GPU — no interconnect topology.</p>
      ) : (
        <p className="text-xs text-zinc-500 text-center py-2">No interconnect links detected.</p>
      )}

      {/* Legend */}
      {links.length > 0 && (
        <div className="flex flex-wrap gap-3 mt-4 pt-4 border-t border-zinc-800 text-[10px] text-zinc-500">
          {[
            { label: 'NVLink', rank: 100 },
            { label: 'PIX', rank: 60 },
            { label: 'PXB', rank: 40 },
            { label: 'PHB', rank: 20 },
            { label: 'SYS', rank: 5 },
          ].map(({ label, rank }) => {
            const style = linkStyle(rank)
            return (
              <span key={label} className="flex items-center gap-1">
                <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
                {label}
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
})
