import { useState, useEffect } from 'react'
import { 
  MessageSquare, Mic, FileText, Workflow, Image, Code,
  ChevronRight, Sparkles, CheckCircle, AlertCircle, X,
  ExternalLink, Zap
} from 'lucide-react'

// Icon mapping
const ICONS = {
  MessageSquare, Mic, FileText, Workflow, Image, Code
}

export function FeatureDiscoveryBanner({ onDismiss }) {
  const [data, setData] = useState(null)
  const [dismissed, setDismissed] = useState(false)
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    fetchFeatures()
  }, [])

  const fetchFeatures = async () => {
    try {
      const res = await fetch('/api/features')
      if (res.ok) {
        setData(await res.json())
      }
    } catch (e) {
      console.error('Failed to fetch features:', e)
    }
  }

  if (!data || dismissed) return null

  const { suggestions = [], summary = {} } = data
  const topSuggestion = suggestions.find(s => !s.blocked)

  if (!topSuggestion || (summary.progress ?? 0) >= 80) return null

  return (
    <div className="liquid-metal-frame mb-6 p-4 bg-gradient-to-r from-theme-accent/10 to-purple-500/10 border border-theme-accent/30 rounded-xl">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-theme-accent/20 rounded-lg">
            <Sparkles className="text-theme-accent" size={20} />
          </div>
          <div>
            <p className="text-theme-text font-medium">{topSuggestion.message}</p>
            <p className="text-theme-text-muted text-sm">
              Setup time: {topSuggestion.setupTime}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setExpanded(topSuggestion.featureId)}
            className="liquid-metal-button px-4 py-2 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
          >
            {topSuggestion.action}
            <ChevronRight size={16} />
          </button>
          <button
            onClick={() => { setDismissed(true); onDismiss?.() }}
            className="p-2 text-theme-text-muted hover:text-theme-text transition-colors"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Expanded instructions */}
      {expanded && (
        <EnableInstructions 
          featureId={expanded} 
          onClose={() => setExpanded(null)} 
        />
      )}
    </div>
  )
}

export function FeatureProgress() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetchFeatures()
  }, [])

  const fetchFeatures = async () => {
    try {
      const res = await fetch('/api/features')
      if (res.ok) {
        setData(await res.json())
      }
    } catch (e) {
      console.error('Failed to fetch features:', e)
    }
  }

  if (!data) return null

  const { summary, gpu } = data

  return (
    <div className="p-4 bg-theme-card border border-theme-border rounded-xl">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-theme-text">Feature Progress</h3>
        <span className="text-xs text-theme-text-muted">{summary.enabled}/{summary.total} enabled</span>
      </div>
      
      {/* Progress bar */}
      <div className="h-2 bg-theme-border rounded-full overflow-hidden mb-3">
        <div 
          className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-500"
          style={{ width: `${summary.progress}%` }}
        />
      </div>

      {/* GPU tier badge */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap size={14} className="text-amber-400" />
          <span className="text-xs text-theme-text-muted">{gpu.name}</span>
        </div>
        <span className={`text-xs px-2 py-0.5 rounded-full ${
          gpu.tier === 'Professional' ? 'bg-purple-500/20 text-purple-400' :
          gpu.tier === 'Prosumer' ? 'bg-theme-accent/20 text-theme-accent' :
          gpu.tier === 'Standard' ? 'bg-blue-500/20 text-blue-400' :
          'bg-theme-border text-theme-text-secondary'
        }`}>
          {gpu.tier} Tier
        </span>
      </div>
    </div>
  )
}

export function FeatureGrid() {
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    fetchFeatures()
  }, [])

  const fetchFeatures = async () => {
    try {
      const res = await fetch('/api/features')
      if (res.ok) {
        setData(await res.json())
      }
    } catch (e) {
      console.error('Failed to fetch features:', e)
    }
  }

  if (!data) return null

  const { features, recommendations } = data

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        {features.map(feature => (
          <FeatureCard 
            key={feature.id}
            feature={feature}
            onClick={() => setSelected(feature)}
          />
        ))}
      </div>

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <div className="p-4 bg-theme-card border border-theme-border rounded-xl">
          <h4 className="text-sm font-semibold text-theme-text mb-2">Recommendations</h4>
          <ul className="space-y-1">
            {recommendations.map((rec, i) => (
              <li key={i} className="text-sm text-theme-text-secondary flex items-center gap-2">
                <CheckCircle size={12} className="text-green-400" />
                {rec}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Selected feature modal */}
      {selected && (
        <EnableInstructions 
          featureId={selected.id}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

function FeatureCard({ feature, onClick }) {
  const Icon = ICONS[feature.icon] || MessageSquare
  
  const statusColors = {
    enabled: 'border-green-500/30 bg-green-500/5',
    available: 'border-theme-accent/30 bg-theme-accent/5 hover:border-theme-accent/50',
    services_needed: 'border-amber-500/30 bg-amber-500/5',
    insufficient_vram: 'border-theme-border bg-theme-card opacity-60'
  }

  const statusIcons = {
    enabled: <CheckCircle size={14} className="text-green-400" />,
    available: <Sparkles size={14} className="text-theme-accent" />,
    services_needed: <AlertCircle size={14} className="text-amber-400" />,
    insufficient_vram: null
  }

  return (
    <button
      onClick={onClick}
      disabled={feature.status === 'insufficient_vram'}
      className={`p-4 rounded-xl border text-left transition-all ${statusColors[feature.status]}`}
    >
      <div className="flex items-center justify-between mb-2">
        <div className={`p-2 rounded-lg ${
          feature.enabled ? 'bg-green-500/20' : 'bg-theme-card'
        }`}>
          <Icon size={18} className={feature.enabled ? 'text-green-400' : 'text-theme-text-muted'} />
        </div>
        {statusIcons[feature.status]}
      </div>
      <h4 className="text-sm font-medium text-theme-text">{feature.name}</h4>
      <p className="text-xs text-theme-text-muted mt-1 line-clamp-2">{feature.description}</p>
      
      {/* VRAM requirement */}
      {feature.requirements.vramGb > 0 && (
        <div className="mt-2 text-xs text-theme-text-muted">
          {feature.requirements.vramGb}GB VRAM
          {!feature.requirements.vramOk && (
            <span className="text-red-400 ml-1">(not enough)</span>
          )}
        </div>
      )}
    </button>
  )
}

function EnableInstructions({ featureId, onClose }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetch(`/api/features/${featureId}/enable`)
      .then(r => r.ok ? r.json() : null)
      .then(setData)
      .catch(console.error)
  }, [featureId])

  if (!data) return null

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-theme-card border border-theme-border rounded-xl max-w-md w-full shadow-2xl">
        <div className="p-6 border-b border-theme-border">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold text-theme-text">Enable {data.name}</h2>
            <button onClick={onClose} className="text-theme-text-muted hover:text-theme-text">
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="p-6 space-y-4">
          {data.instructions.steps.map((step, i) => (
            <div key={i} className="flex items-start gap-3">
              <div className="w-6 h-6 rounded-full bg-theme-card flex items-center justify-center text-xs text-theme-text-muted mt-0.5">
                {i + 1}
              </div>
              <p className="text-theme-text">{step}</p>
            </div>
          ))}
        </div>

        {data.instructions.links?.length > 0 && (
          <div className="p-6 border-t border-theme-border flex flex-wrap gap-2">
            {data.instructions.links.map((link, i) => (
              <a
                key={i}
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                className="px-4 py-2 bg-theme-accent hover:bg-theme-accent-hover text-white rounded-lg text-sm font-medium flex items-center gap-2 transition-colors"
              >
                {link.label}
                <ExternalLink size={14} />
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

