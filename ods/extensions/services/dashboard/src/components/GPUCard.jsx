import { memo } from 'react'
import { Thermometer, Power, HardDrive, Activity } from 'lucide-react'

function Bar({ percent, alert }) {
  const color = alert
    ? 'bg-red-500'
    : percent > 90
      ? 'bg-red-500'
      : percent > 70
        ? 'bg-yellow-500'
        : 'bg-indigo-500'
  return (
    <div className="h-1 bg-zinc-700 rounded-full overflow-hidden mt-1">
      <div
        className={`h-full rounded-full transition-all ${color}`}
        style={{ width: `${Math.min(percent, 100)}%` }}
      />
    </div>
  )
}

export const GPUCard = memo(function GPUCard({ gpu }) {
  const vramPercent = gpu.memory_total_mb > 0
    ? (gpu.memory_used_mb / gpu.memory_total_mb) * 100
    : 0
  const tempAlert = gpu.temperature_c >= 85
  const tempColor = gpu.temperature_c >= 85
    ? 'text-red-400'
    : gpu.temperature_c >= 70
      ? 'text-yellow-400'
      : 'text-zinc-400'

  return (
    <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <span className="text-xs font-mono text-indigo-400 uppercase">GPU {gpu.index}</span>
          <p className="text-sm font-medium text-white leading-tight mt-0.5 truncate max-w-[180px]" title={gpu.name}>
            {gpu.name.replace('NVIDIA ', '').replace('AMD ', '')}
          </p>
        </div>
        <span className="text-xs font-mono text-zinc-500">{gpu.uuid.slice(-8)}</span>
      </div>

      {/* Utilization */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-xs text-zinc-400 mb-1">
          <span className="flex items-center gap-1"><Activity size={12} />Util</span>
          <span className="font-mono text-white">{gpu.utilization_percent}%</span>
        </div>
        <Bar percent={gpu.utilization_percent} />
      </div>

      {/* VRAM */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-xs text-zinc-400 mb-1">
          <span className="flex items-center gap-1"><HardDrive size={12} />VRAM</span>
          <span className="font-mono text-white">
            {(gpu.memory_used_mb / 1024).toFixed(1)}/{(gpu.memory_total_mb / 1024).toFixed(0)} GB
          </span>
        </div>
        <Bar percent={vramPercent} />
      </div>

      {/* Temp + Power */}
      <div className="flex items-center justify-between text-xs mt-3">
        <span className={`flex items-center gap-1 ${tempColor}`}>
          <Thermometer size={12} />
          {gpu.temperature_c}°C{tempAlert ? ' !' : ''}
        </span>
        {gpu.power_w != null ? (
          <span className="flex items-center gap-1 text-zinc-400">
            <Power size={12} />
            {gpu.power_w}W
          </span>
        ) : (
          <span className="text-zinc-600 text-xs">— W</span>
        )}
      </div>

      {/* Assigned services */}
      {gpu.assigned_services?.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-3">
          {gpu.assigned_services.map(svc => (
            <span
              key={svc}
              className="px-1.5 py-0.5 text-[10px] bg-indigo-500/20 text-indigo-300 rounded font-mono"
            >
              {svc}
            </span>
          ))}
        </div>
      )}
    </div>
  )
})
