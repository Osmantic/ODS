import { memo, useState } from 'react'
import { Activity, RefreshCw, AlertTriangle } from 'lucide-react'
import { useGPUDetailed } from '../hooks/useGPUDetailed'
import { GPUCard } from '../components/GPUCard'
import { GPUChart } from '../components/GPUChart'
import { TopologyView } from '../components/TopologyView'
import { AssignmentTable } from '../components/AssignmentTable'

// Aggregate bar shared between aggregate section
const AggBar = memo(function AggBar({ label, value, percent }) {
  const color = percent > 90 ? 'bg-red-500' : percent > 70 ? 'bg-yellow-500' : 'bg-indigo-500'
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-zinc-400">{label}</span>
        <span className="font-mono text-white">{value}</span>
      </div>
      <div className="h-1.5 bg-zinc-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${Math.min(percent, 100)}%` }} />
      </div>
    </div>
  )
})

export default function GPUMonitor() {
  const { detailed, history, topology, loading, error } = useGPUDetailed()
  const [activeTab, setActiveTab] = useState('overview') // 'overview' | 'history'

  if (loading) {
    return (
      <div className="p-8 animate-pulse">
        <div className="h-8 bg-zinc-800 rounded w-1/4 mb-6" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          {[...Array(4)].map((_, i) => <div key={i} className="h-48 bg-zinc-800 rounded-xl" />)}
        </div>
      </div>
    )
  }

  if (error || !detailed) {
    return (
      <div className="p-8">
        <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-sm">
          <AlertTriangle size={18} className="text-red-400 shrink-0" />
          <div>
            <p className="text-white font-medium">GPU data unavailable</p>
            <p className="text-zinc-400 mt-0.5">{error || 'No GPU data returned from API.'}</p>
          </div>
        </div>
      </div>
    )
  }

  const { gpus = [], backend, gpu_count, aggregate, assignment, split_mode, tensor_split } = detailed

  return (
    <div className="p-8">
      {/* Page header */}
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Activity size={22} className="text-indigo-400" />
            GPU Monitor
          </h1>
          <p className="mt-1 text-sm text-zinc-400">
            {gpu_count} GPU{gpu_count !== 1 ? 's' : ''} · <span className="font-mono uppercase text-zinc-300">{backend}</span>
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs font-mono bg-zinc-900/50 border border-zinc-800 rounded-lg px-3 py-2 text-zinc-500">
          <RefreshCw size={12} className="text-indigo-400" />
          live · 5s
        </div>
      </div>

      {/* Aggregate summary strip */}
      {aggregate && gpu_count > 1 && (
        <div className="mb-6 p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-mono text-zinc-400 uppercase tracking-wide">Aggregate</span>
            <span className="text-xs text-zinc-500">{aggregate.name}</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <AggBar
              label="Avg Utilization"
              value={`${aggregate.utilization_percent}%`}
              percent={aggregate.utilization_percent}
            />
            <AggBar
              label="Total VRAM"
              value={`${(aggregate.memory_used_mb / 1024).toFixed(1)}/${(aggregate.memory_total_mb / 1024).toFixed(0)} GB`}
              percent={aggregate.memory_percent}
            />
            <div>
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-zinc-400">Max Temp</span>
                <span className={`font-mono ${aggregate.temperature_c >= 85 ? 'text-red-400' : aggregate.temperature_c >= 70 ? 'text-yellow-400' : 'text-white'}`}>
                  {aggregate.temperature_c}°C
                </span>
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-zinc-400">Total Power</span>
                <span className="font-mono text-white">{aggregate.power_w != null ? `${aggregate.power_w}W` : '—'}</span>
              </div>
            </div>
          </div>
          {/* llama.cpp split info */}
          {(split_mode || tensor_split) && (
            <div className="flex gap-4 mt-3 pt-3 border-t border-zinc-800 text-[10px] font-mono text-zinc-500">
              {split_mode && <span>split_mode=<span className="text-zinc-300">{split_mode}</span></span>}
              {tensor_split && <span>tensor_split=<span className="text-zinc-300">{tensor_split}</span></span>}
            </div>
          )}
        </div>
      )}

      {/* Tab bar */}
      <div className="flex gap-1 mb-6 bg-zinc-900/50 border border-zinc-800 rounded-lg p-1 w-fit">
        {[
          { id: 'overview', label: 'Per-GPU' },
          { id: 'history',  label: 'History' },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
              activeTab === tab.id
                ? 'bg-indigo-600 text-white'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <>
          {/* Per-GPU cards */}
          <div className={`grid gap-4 mb-8 ${gpus.length <= 2 ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-2 lg:grid-cols-4'}`}>
            {gpus.map(gpu => (
              <GPUCard key={gpu.uuid} gpu={gpu} />
            ))}
          </div>

          {/* Topology + Assignment side-by-side when both present */}
          {(topology || assignment) && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {topology && <TopologyView topology={topology} />}
              {assignment && <AssignmentTable assignment={assignment} />}
            </div>
          )}
        </>
      )}

      {activeTab === 'history' && (
        <div className={`grid gap-4 ${gpus.length <= 2 ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-2 lg:grid-cols-4'}`}>
          {gpus.map(gpu => (
            <GPUChart key={gpu.uuid} history={history} gpuIndex={gpu.index} />
          ))}
        </div>
      )}
    </div>
  )
}
