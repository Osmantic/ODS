import { memo } from 'react'
import { Cpu } from 'lucide-react'

const STRATEGY_STYLE = {
  dedicated: 'bg-indigo-500/15 text-indigo-400',
  shared:    'bg-yellow-500/15 text-yellow-400',
  auto:      'bg-zinc-700 text-zinc-300',
}

const PARALLELISM_LABELS = {
  tensor:   'Tensor Parallel',
  pipeline: 'Pipeline Parallel',
  none:     'Single Process',
}

export const AssignmentTable = memo(function AssignmentTable({ assignment }) {
  if (!assignment) return null

  const { strategy, version, services = {} } = assignment
  const serviceEntries = Object.entries(services)
  if (serviceEntries.length === 0) return null

  const strategyStyle = STRATEGY_STYLE[strategy] || STRATEGY_STYLE.auto

  return (
    <div className="p-5 bg-zinc-900/50 border border-zinc-800 rounded-xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Cpu size={16} className="text-indigo-400" />
          <h3 className="text-sm font-semibold text-white">GPU Assignment</h3>
        </div>
        <div className="flex items-center gap-2">
          {version && (
            <span className="text-[10px] font-mono text-zinc-500">v{version}</span>
          )}
          <span className={`px-2 py-0.5 text-[10px] font-mono rounded ${strategyStyle}`}>
            {strategy}
          </span>
        </div>
      </div>

      {/* Service rows */}
      <div className="space-y-2">
        {serviceEntries.map(([name, svcConfig]) => {
          const gpus = svcConfig.gpus || []
          const para = svcConfig.parallelism
          return (
            <div
              key={name}
              className="flex items-start gap-3 p-3 bg-zinc-800/50 rounded-lg"
            >
              {/* Service name */}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-mono text-white truncate">{name}</p>
                {para && (
                  <p className="text-[10px] text-zinc-500 mt-0.5 font-mono">
                    {PARALLELISM_LABELS[para.mode] || para.mode}
                    {para.tensor_parallel_size > 1 && ` · tp=${para.tensor_parallel_size}`}
                    {para.pipeline_parallel_size > 1 && ` · pp=${para.pipeline_parallel_size}`}
                    {para.gpu_memory_utilization != null && ` · mem=${Math.round(para.gpu_memory_utilization * 100)}%`}
                  </p>
                )}
              </div>

              {/* GPU badges */}
              <div className="flex flex-wrap gap-1 shrink-0">
                {gpus.map(uuid => (
                  <span
                    key={uuid}
                    className="px-1.5 py-0.5 text-[10px] font-mono bg-zinc-700 text-zinc-300 rounded"
                    title={uuid}
                  >
                    {uuid.startsWith('GPU-') ? uuid.slice(-8) : uuid}
                  </span>
                ))}
                {gpus.length === 0 && (
                  <span className="text-[10px] text-zinc-600 italic">no GPUs</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
})
