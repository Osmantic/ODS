import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ExternalLink, GitBranch, RefreshCw, X } from 'lucide-react'

const POLL_INTERVAL = 10000
const NODE_W = 170
const NODE_H = 64
const LABEL_W = 210
const LAYER_GAP = 190
const NODE_GAP = 42
const MIN_W = 1080
const MIN_H = 720

const LAYERS = ['core', 'middleware', 'user-facing', 'other']
const LAYER_LABELS = {
  core: 'CORE',
  middleware: 'MIDDLEWARE',
  'user-facing': 'USER FACING',
  other: 'OTHER',
}

const CATEGORY_MAP = {
  'llama-server': 'core',
  qdrant: 'core',
  searxng: 'core',
  embeddings: 'core',
  whisper: 'core',
  tts: 'core',
  litellm: 'middleware',
  'dashboard-api': 'middleware',
  'token-spy': 'middleware',
  'privacy-shield': 'middleware',
  langfuse: 'middleware',
  ape: 'middleware',
  'open-webui': 'user-facing',
  perplexica: 'user-facing',
  n8n: 'user-facing',
  openclaw: 'user-facing',
  dashboard: 'user-facing',
  comfyui: 'user-facing',
  opencode: 'user-facing',
}

const NAME_TO_ID = {
  'APE (Agent Policy Engine)': 'ape',
  'ComfyUI (Image Generation)': 'comfyui',
  'Dashboard (Control Center)': 'dashboard',
  'Dashboard API (System Status)': 'dashboard-api',
  'Kokoro (TTS)': 'tts',
  'LiteLLM (API Gateway)': 'litellm',
  'llama-server (LLM Inference)': 'llama-server',
  'n8n (Workflows)': 'n8n',
  'Open WebUI (Chat)': 'open-webui',
  'OpenClaw (Agents)': 'openclaw',
  'OpenCode (IDE)': 'opencode',
  'Perplexica (Deep Research)': 'perplexica',
  'Privacy Shield (PII Protection)': 'privacy-shield',
  'Qdrant (Vector DB)': 'qdrant',
  'SearXNG (Web Search)': 'searxng',
  'TEI (Embeddings)': 'embeddings',
  'Token Spy (Usage Monitor)': 'token-spy',
  'Whisper (STT)': 'whisper',
}

// source depends on target. Unknown extension dependencies are not guessed.
const KNOWN_EDGES = [
  ['open-webui', 'litellm', 'LLM proxy'],
  ['litellm', 'llama-server', 'inference'],
  ['perplexica', 'searxng', 'search'],
  ['perplexica', 'litellm', 'LLM proxy'],
  ['n8n', 'litellm', 'LLM proxy'],
  ['n8n', 'qdrant', 'vector store'],
  ['openclaw', 'litellm', 'LLM proxy'],
  ['openclaw', 'qdrant', 'vector store'],
  ['litellm', 'langfuse', 'observability'],
  ['qdrant', 'embeddings', 'embeddings'],
  ['open-webui', 'whisper', 'voice input'],
  ['open-webui', 'tts', 'voice output'],
  ['dashboard', 'dashboard-api', 'API'],
  ['dashboard-api', 'llama-server', 'API'],
  ['token-spy', 'litellm', 'intercept'],
  ['privacy-shield', 'litellm', 'privacy'],
  ['comfyui', 'open-webui', 'API'],
  ['ape', 'litellm', 'LLM proxy'],
]

const EDGE_META = {
  inference: '#a855f7',
  'LLM proxy': '#3b82f6',
  search: '#f97316',
  'vector store': '#06b6d4',
  embeddings: '#14b8a6',
  'voice input': '#ec4899',
  'voice output': '#ec4899',
  API: '#6366f1',
  intercept: '#f59e0b',
  observability: '#84cc16',
  privacy: '#f43f5e',
}

const STATUS = {
  healthy: { color: '#22c55e', text: 'text-green-400', dot: 'bg-green-400' },
  degraded: { color: '#eab308', text: 'text-yellow-400', dot: 'bg-yellow-400' },
  unhealthy: { color: '#ef4444', text: 'text-red-400', dot: 'bg-red-400' },
  down: { color: '#ef4444', text: 'text-red-400', dot: 'bg-red-400' },
  not_deployed: { color: '#6b7280', text: 'text-zinc-500', dot: 'bg-zinc-500' },
  unknown: { color: '#6b7280', text: 'text-zinc-500', dot: 'bg-zinc-500' },
}

function statusMeta(status) {
  return STATUS[status] || STATUS.unknown
}

function normalizeStatus(status) {
  return status || 'unknown'
}

function slugServiceName(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/\([^)]*\)/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function resolveServiceId(service) {
  const explicitId = service.id || service.service_id || service.key
  if (explicitId) return explicitId
  return NAME_TO_ID[service.name] || slugServiceName(service.name)
}

export function buildTopology(statusData) {
  const services = Array.isArray(statusData?.services) ? statusData.services : []
  const nodes = services
    .map(service => {
      const id = resolveServiceId(service)
      if (!id) return null
      return {
        id,
        name: service.name || id,
        status: normalizeStatus(service.status),
        port: service.external_port || service.port || '',
        category: CATEGORY_MAP[id] || 'other',
      }
    })
    .filter(Boolean)
  const nodeById = new Map(nodes.map(node => [node.id, node]))
  const edges = KNOWN_EDGES
    .filter(([source, target]) => nodeById.has(source) && nodeById.has(target))
    .map(([source, target, label]) => ({
      source,
      target,
      label,
      status: nodeById.get(source).status === 'healthy' && nodeById.get(target).status === 'healthy'
        ? 'healthy'
        : 'degraded',
    }))
  return { nodes, edges }
}

function computeLayout(nodes) {
  const rows = Object.fromEntries(LAYERS.map(layer => [layer, []]))
  for (const node of nodes) rows[node.category || 'other']?.push(node)
  for (const row of Object.values(rows)) row.sort((a, b) => a.name.localeCompare(b.name))

  const maxCount = Math.max(1, ...Object.values(rows).map(row => row.length))
  const svgWidth = Math.max(MIN_W, LABEL_W + maxCount * NODE_W + (maxCount - 1) * NODE_GAP + 120)
  const positions = {}
  const layerY = {}
  let y = 80

  for (const layer of LAYERS) {
    const row = rows[layer]
    if (row.length === 0) continue
    const rowWidth = row.length * NODE_W + Math.max(0, row.length - 1) * NODE_GAP
    const x0 = LABEL_W + Math.max(40, (svgWidth - LABEL_W - rowWidth) / 2)
    row.forEach((node, index) => {
      positions[node.id] = { x: x0 + index * (NODE_W + NODE_GAP), y }
    })
    layerY[layer] = y
    y += LAYER_GAP
  }

  return { positions, layerY, svgWidth, svgHeight: Math.max(MIN_H, y + 40) }
}

function edgePath(source, target) {
  const sx = source.x + NODE_W / 2
  const sy = source.y + (source.y > target.y ? 0 : NODE_H)
  const tx = target.x + NODE_W / 2
  const ty = target.y + (source.y > target.y ? NODE_H : 0)
  const midY = (sy + ty) / 2
  return `M ${sx} ${sy} L ${sx} ${midY} L ${tx} ${midY} L ${tx} ${ty}`
}

function ServiceNode({ node, pos, selected, onSelect }) {
  const meta = statusMeta(node.status)
  return (
    <g onClick={() => onSelect(node)} className="cursor-pointer">
      {selected && (
        <rect x={pos.x - 4} y={pos.y - 4} width={NODE_W + 8} height={NODE_H + 8} rx={14} fill="none" stroke={meta.color} strokeWidth="2" />
      )}
      <rect x={pos.x} y={pos.y} width={NODE_W} height={NODE_H} rx={12} className="fill-zinc-900 stroke-zinc-700" />
      <circle cx={pos.x + 15} cy={pos.y + 25} r="4" fill={meta.color} />
      <text x={pos.x + 27} y={pos.y + 29} className="fill-zinc-100" style={{ fontSize: 12, fontWeight: 700 }}>
        {node.name.length > 18 ? `${node.name.slice(0, 17)}…` : node.name}
      </text>
      <text x={pos.x + 15} y={pos.y + 47} className="fill-zinc-500" style={{ fontSize: 10, fontFamily: 'monospace' }}>
        :{node.port}
      </text>
      <text x={pos.x + NODE_W - 10} y={pos.y + 47} textAnchor="end" style={{ fontSize: 9, fill: meta.color }}>
        {node.status}
      </text>
    </g>
  )
}

function DetailPanel({ node, edges, onClose }) {
  if (!node) return null
  const meta = statusMeta(node.status)
  const upstream = edges.filter(edge => edge.target === node.id)
  const downstream = edges.filter(edge => edge.source === node.id)
  const serviceUrl = node.port ? `http://${window.location.hostname}:${node.port}` : null

  return (
    <div className="absolute top-4 right-4 z-10 w-72 overflow-hidden rounded-xl border border-theme-border bg-theme-card shadow-2xl">
      <div className="flex items-center justify-between border-b border-theme-border px-4 py-3">
        <div className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${meta.dot}`} />
          <span className="text-sm font-semibold text-theme-text">{node.name}</span>
        </div>
        <button onClick={onClose} className="text-theme-text-muted hover:text-theme-text"><X size={16} /></button>
      </div>
      <div className="space-y-3 px-4 py-3 text-xs">
        <div className="flex justify-between"><span className="text-theme-text-muted">Status</span><span className={meta.text}>{node.status}</span></div>
        <div className="flex justify-between"><span className="text-theme-text-muted">Port</span><span className="font-mono text-theme-text">{node.port}</span></div>
        <div className="flex justify-between"><span className="text-theme-text-muted">Layer</span><span className="text-theme-text">{node.category}</span></div>
        {upstream.length > 0 && <DependencyList label="Used by" edges={upstream} field="source" />}
        {downstream.length > 0 && <DependencyList label="Depends on" edges={downstream} field="target" />}
        {serviceUrl && <a href={serviceUrl} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-theme-accent hover:underline"><ExternalLink size={12} />Open service</a>}
      </div>
    </div>
  )
}

function DependencyList({ label, edges, field }) {
  return (
    <div>
      <span className="mb-1 block text-theme-text-muted">{label}:</span>
      <div className="space-y-1">
        {edges.map(edge => (
          <div key={`${edge.source}-${edge.target}`} className="flex items-center gap-1.5 text-theme-text">
            <span style={{ color: EDGE_META[edge.label] || '#6b7280', fontSize: 10 }}>●</span>
            {edge[field]}
            <span className="ml-auto text-theme-text-muted">({edge.label})</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function ServiceMap() {
  const [topology, setTopology] = useState({ nodes: [], edges: [] })
  const [selectedNode, setSelectedNode] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const fetchInFlight = useRef(false)

  const fetchTopology = useCallback(async () => {
    if (document.hidden || fetchInFlight.current) return
    fetchInFlight.current = true
    try {
      const response = await fetch('/api/status')
      if (!response.ok) throw new Error('Failed to fetch service status')
      setTopology(buildTopology(await response.json()))
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      fetchInFlight.current = false
    }
  }, [])

  useEffect(() => {
    fetchTopology()
    const interval = setInterval(fetchTopology, POLL_INTERVAL)
    const onVisibility = () => { if (!document.hidden) fetchTopology() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [fetchTopology])

  const { nodes, edges } = topology
  const { positions, layerY, svgWidth, svgHeight } = useMemo(() => computeLayout(nodes), [nodes])
  const counts = useMemo(() => ({
    healthy: nodes.filter(node => node.status === 'healthy').length,
    degraded: nodes.filter(node => node.status === 'degraded').length,
    down: nodes.filter(node => node.status === 'down' || node.status === 'unhealthy').length,
    other: nodes.filter(node => !['healthy', 'degraded', 'down', 'unhealthy'].includes(node.status)).length,
  }), [nodes])
  const edgeLabels = [...new Set(edges.map(edge => edge.label))]

  if (loading) {
    return <div className="p-8 animate-pulse"><div className="mb-6 h-8 w-1/3 rounded bg-theme-card" /><div className="h-96 rounded-xl bg-theme-card" /></div>
  }

  if (error) {
    return <div className="p-8 text-sm text-red-400">Topology data unavailable: {error}</div>
  }

  return (
    <div className="p-8">
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-theme-text"><GitBranch size={22} className="text-theme-accent" />Integrations</h1>
          <p className="mt-1 text-sm text-theme-text-muted">
            {nodes.length} services · <span className="text-green-400">{counts.healthy} healthy</span>
            {counts.degraded > 0 && <>, <span className="text-yellow-400">{counts.degraded} degraded</span></>}
            {counts.down > 0 && <>, <span className="text-red-400">{counts.down} down</span></>}
            {counts.other > 0 && <>, <span className="text-zinc-500">{counts.other} other</span></>}
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-theme-border bg-theme-card px-3 py-2 font-mono text-xs text-theme-text-muted"><RefreshCw size={12} className="text-theme-accent" />live · 10s</div>
      </div>

      <div className="mb-4 flex flex-wrap gap-4 text-xs text-theme-text-muted">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-green-400" />Healthy</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-yellow-400" />Degraded</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-red-400" />Down</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-zinc-500" />Not deployed</span>
      </div>

      <div className="relative min-h-[70vh] overflow-auto rounded-xl border border-theme-border bg-theme-card">
        <svg width={svgWidth} height={svgHeight} viewBox={`0 0 ${svgWidth} ${svgHeight}`} className="mx-auto block">
          <defs>
            <filter id="node-shadow" x="-25%" y="-60%" width="150%" height="230%"><feDropShadow dx="0" dy="2" stdDeviation="10" floodColor="#000" floodOpacity="0.7" /></filter>
            {Object.entries(EDGE_META).map(([label, color]) => <marker key={label} id={`arrow-${label.replaceAll(' ', '-')}`} markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><path d="M 0 0 L 7 2.5 L 0 5 Z" fill={color} fillOpacity="0.85" /></marker>)}
          </defs>

          {LAYERS.map(layer => layerY[layer] == null ? null : <text key={layer} x="32" y={layerY[layer] + NODE_H / 2} className="fill-zinc-600" style={{ fontSize: 10, fontWeight: 700 }}>{LAYER_LABELS[layer]}</text>)}

          {edges.map(edge => {
            const source = positions[edge.source]
            const target = positions[edge.target]
            if (!source || !target) return null
            const color = EDGE_META[edge.label] || '#6b7280'
            return <path key={`${edge.source}-${edge.target}`} d={edgePath(source, target)} fill="none" stroke={color} strokeWidth="1.8" strokeOpacity={edge.status === 'healthy' ? 0.72 : 0.32} strokeDasharray={edge.status === 'healthy' ? undefined : '5 4'} markerEnd={`url(#arrow-${edge.label.replaceAll(' ', '-')})`} />
          })}

          {nodes.map(node => positions[node.id] && <ServiceNode key={node.id} node={node} pos={positions[node.id]} selected={selectedNode?.id === node.id} onSelect={setSelectedNode} />)}
        </svg>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-theme-border px-4 py-3">
          <span className="text-xs font-medium text-zinc-600">Connections:</span>
          {edgeLabels.map(label => <span key={label} className="flex items-center gap-1.5 text-xs text-theme-text-muted"><span className="inline-block h-2 w-2 rounded-full" style={{ background: EDGE_META[label] || '#6b7280' }} />{label}</span>)}
        </div>

        <DetailPanel node={selectedNode} edges={edges} onClose={() => setSelectedNode(null)} />
      </div>
    </div>
  )
}
