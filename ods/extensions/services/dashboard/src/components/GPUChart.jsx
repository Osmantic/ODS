import { memo } from 'react'

// SVG polyline sparkline — no external deps
function Sparkline({ values, color, height = 48, width = '100%' }) {
  const data = (values || []).filter(v => v != null)
  if (data.length < 2) {
    return <div className="h-12 bg-zinc-800/50 rounded" />
  }

  const W = 300 // internal viewBox width
  const H = height
  const max = Math.max(...data, 1)
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * W
      const y = H - (v / max) * (H - 4) - 2
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ width, height }}
      className="block"
    >
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
    </svg>
  )
}

const METRICS = [
  { key: 'utilization', label: 'Utilization %', color: '#818cf8', max: 100 },
  { key: 'memory_percent', label: 'VRAM %', color: '#34d399', max: 100 },
  { key: 'temperature', label: 'Temp °C', color: '#fb923c', max: null },
  { key: 'power_w', label: 'Power W', color: '#a78bfa', max: null },
]

// history shape: { timestamps: [...], gpus: { "0": { utilization: [], memory_percent: [], temperature: [], power_w: [] }, ... } }
export const GPUChart = memo(function GPUChart({ history, gpuIndex }) {
  const key = String(gpuIndex)
  const gpuData = history?.gpus?.[key]

  if (!gpuData || !history?.timestamps?.length) {
    return (
      <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
        <p className="text-xs text-zinc-500 text-center py-6">No history yet — collecting samples...</p>
      </div>
    )
  }

  const timestamps = history.timestamps
  const first = timestamps[0]
  const last = timestamps[timestamps.length - 1]
  const timeRange = first && last
    ? `${new Date(first).toLocaleTimeString()} – ${new Date(last).toLocaleTimeString()}`
    : ''

  return (
    <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-mono text-indigo-400 uppercase">GPU {gpuIndex} History</span>
        <span className="text-[10px] text-zinc-500 font-mono">{timeRange}</span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        {METRICS.map(({ key: mk, label, color }) => {
          const values = gpuData[mk] || []
          const latest = values[values.length - 1]
          return (
            <div key={mk}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-zinc-500">{label}</span>
                <span className="text-[10px] font-mono" style={{ color }}>
                  {latest != null ? (Number.isInteger(latest) ? latest : latest.toFixed(1)) : '—'}
                </span>
              </div>
              <Sparkline values={values} color={color} height={36} />
            </div>
          )
        })}
      </div>
    </div>
  )
})
