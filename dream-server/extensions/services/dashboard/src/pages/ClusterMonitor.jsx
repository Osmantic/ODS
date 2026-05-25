import { Network, RefreshCw, AlertTriangle, Server, Cpu, Wifi, WifiOff } from 'lucide-react'
import { useClusterStatus } from '../hooks/useClusterStatus'

function formatVram(gpus) {
  if (!gpus || gpus.length === 0) return 'N/A'
  const totalMb = gpus.reduce((sum, g) => sum + (g.vram_mb || 0), 0)
  if (totalMb === 0) return 'CPU only'
  return `${(totalMb / 1024).toFixed(1)} GB`
}

function gpuName(gpus) {
  if (!gpus || gpus.length === 0) return 'Unknown'
  const names = gpus.map(g => g.name || 'Unknown')
  if (names.length === 1) return names[0]
  return `${names[0]} + ${names.length - 1} more`
}

function StatusBadge({ status }) {
  const online = status === 'online'
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${
      online ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
    }`}>
      {online ? <Wifi size={12} /> : <WifiOff size={12} />}
      {online ? 'Online' : 'Offline'}
    </span>
  )
}

function NodeCard({ title, subtitle, gpus, status, ping, backend, isController }) {
  return (
    <div className="bg-zinc-900/50 border border-zinc-800 rounded-xl p-4">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          {isController ? <Server size={16} className="text-indigo-400" /> : <Cpu size={16} className="text-zinc-400" />}
          <div>
            <h3 className="text-sm font-medium text-white">{title}</h3>
            <p className="text-xs text-zinc-500">{subtitle}</p>
          </div>
        </div>
        <StatusBadge status={status} />
      </div>
      <div className="space-y-2 text-xs">
        <div className="flex justify-between">
          <span className="text-zinc-400">GPU</span>
          <span className="text-white font-mono">{gpuName(gpus)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-400">VRAM</span>
          <span className="text-white font-mono">{formatVram(gpus)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-400">Backend</span>
          <span className="text-white font-mono uppercase">{backend || '---'}</span>
        </div>
        {ping != null && (
          <div className="flex justify-between">
            <span className="text-zinc-400">Ping</span>
            <span className={`font-mono ${ping < 10 ? 'text-emerald-400' : ping < 50 ? 'text-yellow-400' : 'text-red-400'}`}>
              {ping} ms
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

export default function ClusterMonitor() {
  const { cluster, loading, error, refetch } = useClusterStatus()

  if (loading) {
    return (
      <div className="p-8 animate-pulse">
        <div className="h-8 bg-zinc-800 rounded w-1/4 mb-6" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(3)].map((_, i) => <div key={i} className="h-48 bg-zinc-800 rounded-xl" />)}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-8">
        <div className="flex items-start gap-3 p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-sm">
          <AlertTriangle size={18} className="text-red-400 shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-white font-medium">Cluster data unavailable</p>
            <p className="text-zinc-400 mt-0.5">{error}</p>
          </div>
          <button
            type="button"
            onClick={refetch}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-xs font-medium text-white transition-colors"
          >
            <RefreshCw size={12} />
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!cluster || !cluster.enabled) {
    return (
      <div className="p-8">
        <div className="flex items-center gap-3 p-4 bg-zinc-800/50 border border-zinc-700 rounded-xl text-sm">
          <Network size={18} className="text-zinc-400 shrink-0" />
          <p className="text-zinc-400">LAN cluster mode is not enabled. Run <code className="text-zinc-300 bg-zinc-800 px-1.5 py-0.5 rounded">dream cluster enable</code> to get started.</p>
        </div>
      </div>
    )
  }

  const { controller, nodes, tensor_split, worker_list } = cluster
  const totalNodes = 1 + nodes.length
  const onlineNodes = 1 + nodes.filter(n => n.status === 'online').length

  return (
    <div className="p-8">
      {/* Page header */}
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Network size={22} className="text-indigo-400" />
            LAN Cluster
          </h1>
          <p className="mt-1 text-sm text-zinc-400">
            {onlineNodes}/{totalNodes} node{totalNodes !== 1 ? 's' : ''} online
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs font-mono bg-zinc-900/50 border border-zinc-800 rounded-lg px-3 py-2 text-zinc-500">
          <RefreshCw size={12} className="text-indigo-400" />
          live &middot; 5s
        </div>
      </div>

      {/* Cluster info strip */}
      {(tensor_split || worker_list) && (
        <div className="mb-6 p-3 bg-zinc-900/50 border border-zinc-800 rounded-xl flex flex-wrap gap-4 text-[11px] font-mono text-zinc-500">
          {worker_list && <span>workers=<span className="text-zinc-300">{worker_list}</span></span>}
          {tensor_split && <span>tensor_split=<span className="text-zinc-300">{tensor_split}</span></span>}
        </div>
      )}

      {/* Node cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {controller && (
          <NodeCard
            title="Controller"
            subtitle={controller.ip}
            gpus={controller.gpus}
            status="online"
            backend={controller.gpu_backend}
            isController
          />
        )}
        {nodes.map((node) => (
          <NodeCard
            key={`${node.ip}:${node.rpc_port}`}
            title={`Worker`}
            subtitle={`${node.ip}:${node.rpc_port}`}
            gpus={node.gpus}
            status={node.status}
            ping={node.ping_ms}
            backend={node.gpu_backend}
          />
        ))}
      </div>

      {nodes.length === 0 && (
        <div className="mt-6 p-4 bg-zinc-800/50 border border-zinc-700 rounded-xl text-sm text-zinc-400 text-center">
          No workers connected yet. Run <code className="text-zinc-300 bg-zinc-800 px-1.5 py-0.5 rounded">dream cluster join</code> on a worker machine.
        </div>
      )}
    </div>
  )
}
