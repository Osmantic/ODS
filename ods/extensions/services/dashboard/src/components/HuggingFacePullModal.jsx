import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, ArrowLeft, Check, Loader2, Search, X } from 'lucide-react'

const COMFYUI_SUBDIRS = [
  'checkpoints', 'diffusion_models', 'text_encoders', 'vae', 'loras',
  'controlnet', 'upscale_models', 'clip_vision', 'embeddings', 'unet',
  'clip', 'vae_approx', 'gligen', 'hypernetworks', 'style_models',
  'photomaker', 'model_patches', 'audio_encoders', 'configs', 'diffusers',
  'latent_upscale_models',
]

async function responseError(resp, label) {
  const body = await resp.json().catch(() => ({}))
  const detail = Array.isArray(body.detail) ? body.detail[0]?.msg : body.detail
  return new Error(detail || `${label} failed: ${resp.status}`)
}

// Heuristic only — always overridable in the confirm step. Order matters:
// more specific component keywords are checked before the generic fallback.
function suggestTarget(filename) {
  const name = filename.toLowerCase()
  if (name.endsWith('.gguf')) return 'llama-server'
  if (name.includes('vae')) return 'comfyui:vae'
  if (name.includes('lora') || name.includes('lokr') || name.includes('loha')) return 'comfyui:loras'
  if (name.includes('controlnet') || name.includes('control_net')) return 'comfyui:controlnet'
  if (name.includes('upscal') || name.includes('esrgan')) return 'comfyui:upscale_models'
  if (name.includes('clip_vision')) return 'comfyui:clip_vision'
  if (name.includes('clip') || name.includes('t5') || name.includes('text_encoder') || name.includes('encoder')) return 'comfyui:text_encoders'
  if (name.includes('embedding') || name.includes('textual_inversion')) return 'comfyui:embeddings'
  return 'comfyui:checkpoints'
}

function formatBytes(bytes) {
  if (!bytes) return ''
  const gb = bytes / (1024 ** 3)
  if (gb >= 1) return `${gb.toFixed(2)} GB`
  const mb = bytes / (1024 ** 2)
  if (mb >= 1) return `${mb.toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(0)} KB`
}

// Collapses HF's <base>-00001-of-00005.<ext> shard convention (flagged by the
// backend via group_key) into one selectable item per logical file. Files
// without a group_key are independent components and stay as their own item.
function buildItems(files) {
  const groups = new Map()
  const items = []
  for (const file of files) {
    if (file.group_key) {
      let group = groups.get(file.group_key)
      if (!group) {
        group = { key: `group:${file.group_key}`, kind: 'group', label: file.group_key, files: [], totalSize: 0 }
        groups.set(file.group_key, group)
        items.push(group)
      }
      group.files.push(file)
      group.totalSize += file.size || 0
    } else {
      items.push({ key: `single:${file.filename}`, kind: 'single', label: file.filename, files: [file], totalSize: file.size || 0 })
    }
  }
  for (const item of items) {
    if (item.kind === 'group') item.files.sort((a, b) => (a.part || 0) - (b.part || 0))
  }
  return items
}

function itemIsGguf(item) {
  return item.files.every(f => f.filename.toLowerCase().endsWith('.gguf'))
}

export function HuggingFacePullModal({ onClose, onPulled }) {
  const [authChecked, setAuthChecked] = useState(false)
  const [configured, setConfigured] = useState(false)
  const [step, setStep] = useState('search')
  const [error, setError] = useState(null)

  // Connect-account step state
  const [token, setToken] = useState('')
  const [savingToken, setSavingToken] = useState(false)

  // Search step state
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState([])

  // Files step state
  const [selectedRepo, setSelectedRepo] = useState(null)
  const [filesLoading, setFilesLoading] = useState(false)
  const [files, setFiles] = useState([])
  const [selectedKeys, setSelectedKeys] = useState(() => new Set())

  // Confirm step state
  const [targetByKey, setTargetByKey] = useState({})
  const [pulling, setPulling] = useState(false)

  const items = useMemo(() => buildItems(files), [files])
  const selectedItems = useMemo(() => items.filter(item => selectedKeys.has(item.key)), [items, selectedKeys])
  const selectedTotalSize = useMemo(() => selectedItems.reduce((sum, item) => sum + item.totalSize, 0), [selectedItems])

  useEffect(() => {
    let cancelled = false
    fetch('/api/models/hf/auth')
      .then(resp => resp.ok ? resp.json() : { configured: false })
      .then(data => {
        if (cancelled) return
        setConfigured(Boolean(data.configured))
        setStep(data.configured ? 'search' : 'connect')
        setAuthChecked(true)
      })
      .catch(() => {
        if (cancelled) return
        setAuthChecked(true)
      })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (step !== 'search' || !query.trim()) {
      setResults([])
      setSearching(false)
      return
    }
    let cancelled = false
    setSearching(true)
    const handle = setTimeout(() => {
      fetch(`/api/models/hf/search?q=${encodeURIComponent(query.trim())}`)
        .then(async resp => {
          if (!resp.ok) throw await responseError(resp, 'Search')
          return resp.json()
        })
        .then(data => { if (!cancelled) setResults(data) })
        .catch(err => { if (!cancelled) setError(err.message) })
        .finally(() => { if (!cancelled) setSearching(false) })
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [query, step])

  const handleSaveToken = async () => {
    if (!token.trim()) return
    setSavingToken(true)
    setError(null)
    try {
      const resp = await fetch('/api/models/hf/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token.trim() }),
      })
      if (!resp.ok) throw await responseError(resp, 'Connect account')
      setConfigured(true)
      setToken('')
      setStep('search')
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingToken(false)
    }
  }

  const handleSelectRepo = async (repo) => {
    setSelectedRepo(repo)
    setFilesLoading(true)
    setError(null)
    setSelectedKeys(new Set())
    // Clear immediately: if this fetch fails, the previous repo's files must
    // not remain rendered (and pullable) under the new repo's heading.
    setFiles([])
    setStep('files')
    try {
      const resp = await fetch(`/api/models/hf/files?repo_id=${encodeURIComponent(repo.id)}&revision=main`)
      if (!resp.ok) throw await responseError(resp, 'List files')
      setFiles(await resp.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setFilesLoading(false)
    }
  }

  const toggleItem = (key) => {
    setSelectedKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleContinueToConfirm = () => {
    const suggestions = {}
    for (const item of selectedItems) {
      suggestions[item.key] = itemIsGguf(item) ? 'llama-server' : suggestTarget(item.files[0].filename)
    }
    setTargetByKey(suggestions)
    setStep('confirm')
  }

  const handlePull = async () => {
    setPulling(true)
    setError(null)
    try {
      // filename in the API is the bare name the file lands as locally;
      // repo_path is where it lives inside the repo (may be nested).
      const payloadFiles = selectedItems.flatMap(item =>
        item.files.map(file => ({
          filename: file.filename.split('/').pop(),
          repo_path: file.filename,
          sha256: file.sha256 || null,
          size: file.size || null,
          target: targetByKey[item.key],
        }))
      )
      const resp = await fetch('/api/models/pull', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_id: selectedRepo.id,
          revision: 'main',
          files: payloadFiles,
        }),
      })
      if (!resp.ok) throw await responseError(resp, 'Pull')
      onPulled?.()
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setPulling(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-xl border border-theme-border bg-theme-card p-6"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Pull from Hugging Face"
      >
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {step !== 'search' && step !== 'connect' && (
              <button
                type="button"
                onClick={() => setStep(step === 'confirm' ? 'files' : 'search')}
                className="text-theme-text-muted hover:text-theme-text"
                aria-label="Back"
              >
                <ArrowLeft size={18} />
              </button>
            )}
            <h2 className="text-lg font-semibold text-theme-text">Pull from Hugging Face</h2>
          </div>
          <button type="button" onClick={onClose} className="text-theme-text-muted hover:text-theme-text" aria-label="Close">
            <X size={20} />
          </button>
        </div>

        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {!authChecked && (
          <div className="flex items-center justify-center py-8 text-theme-text-muted">
            <Loader2 size={20} className="animate-spin" />
          </div>
        )}

        {authChecked && step === 'connect' && (
          <div>
            <p className="mb-3 text-sm text-theme-text-muted">
              Connect a Hugging Face account to search and pull models, including gated/private repos.
              Public repos work without one too — you can skip this.
            </p>
            <input
              type="password"
              value={token}
              onChange={e => setToken(e.target.value)}
              placeholder="hf_..."
              autoFocus
              className="mb-3 w-full rounded-lg border border-theme-border bg-theme-bg px-3 py-2 text-theme-text focus:border-theme-accent focus:outline-none"
            />
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setStep('search')} className="px-4 py-2 text-theme-text-muted hover:text-theme-text">
                Skip
              </button>
              <button
                type="button"
                onClick={handleSaveToken}
                disabled={savingToken || !token.trim()}
                className="flex items-center gap-2 rounded-lg bg-theme-accent px-4 py-2 text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {savingToken && <Loader2 size={16} className="animate-spin" />}
                Connect
              </button>
            </div>
          </div>
        )}

        {authChecked && step === 'search' && (
          <div>
            <div className="mb-3 flex items-center gap-2 rounded-lg border border-theme-border bg-theme-bg px-3 py-2">
              <Search size={15} className="text-theme-text-muted" />
              <input
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Search Hugging Face models..."
                autoFocus
                className="w-full bg-transparent text-sm text-theme-text focus:outline-none"
              />
              {searching && <Loader2 size={14} className="animate-spin text-theme-text-muted" />}
            </div>
            {!configured && (
              <button type="button" onClick={() => setStep('connect')} className="mb-3 text-xs text-theme-accent hover:underline">
                Connect account for gated repos
              </button>
            )}
            <div className="max-h-72 space-y-1 overflow-y-auto">
              {results.map(repo => (
                <button
                  key={repo.id}
                  type="button"
                  onClick={() => handleSelectRepo(repo)}
                  className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm hover:bg-white/[0.05]"
                >
                  <span className="truncate text-theme-text">{repo.id}</span>
                  <span className="ml-2 flex shrink-0 items-center gap-2 text-[11px] text-theme-text-muted">
                    {repo.gated && <span className="rounded border border-amber-400/25 bg-amber-500/12 px-1.5 py-0.5 text-amber-300">gated</span>}
                    {repo.downloads?.toLocaleString()} DL
                  </span>
                </button>
              ))}
              {query.trim() && !searching && results.length === 0 && (
                <p className="px-3 py-2 text-sm text-theme-text-muted">No models found.</p>
              )}
            </div>
          </div>
        )}

        {authChecked && step === 'files' && (
          <div>
            <p className="mb-3 truncate text-sm text-theme-text-muted">{selectedRepo?.id}</p>
            {filesLoading ? (
              <div className="flex items-center justify-center py-8 text-theme-text-muted">
                <Loader2 size={20} className="animate-spin" />
              </div>
            ) : (
              <>
                <div className="max-h-72 space-y-1 overflow-y-auto">
                  {items.map(item => {
                    const isSelected = selectedKeys.has(item.key)
                    return (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => toggleItem(item.key)}
                        className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                          isSelected ? 'bg-theme-accent/12 hover:bg-theme-accent/16' : 'hover:bg-white/[0.05]'
                        }`}
                      >
                        <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                          isSelected ? 'border-theme-accent bg-theme-accent' : 'border-theme-border'
                        }`}>
                          {isSelected && <Check size={11} className="text-white" />}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-theme-text">
                          {item.kind === 'group' ? `${item.label} (${item.files.length} parts)` : item.label}
                        </span>
                        <span className="ml-2 shrink-0 font-mono text-[11px] text-theme-text-muted">{formatBytes(item.totalSize)}</span>
                      </button>
                    )
                  })}
                  {items.length === 0 && (
                    <p className="px-3 py-2 text-sm text-theme-text-muted">No .gguf or .safetensors files in this repo.</p>
                  )}
                </div>
                {selectedItems.length > 0 && (
                  <div className="mt-3 flex justify-end">
                    <button
                      type="button"
                      onClick={handleContinueToConfirm}
                      className="rounded-lg bg-theme-accent px-4 py-2 text-sm text-white transition-opacity hover:opacity-90"
                    >
                      Continue with {selectedItems.length} {selectedItems.length === 1 ? 'item' : 'items'} ({formatBytes(selectedTotalSize)})
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {authChecked && step === 'confirm' && selectedItems.length > 0 && (
          <div>
            <p className="mb-3 truncate text-sm text-theme-text">{selectedRepo?.id}</p>
            <div className="mb-4 max-h-72 space-y-3 overflow-y-auto">
              {selectedItems.map(item => (
                <div key={item.key} className="rounded-lg border border-theme-border p-3">
                  <p className="mb-2 truncate font-mono text-xs text-theme-text-muted">
                    {item.kind === 'group' ? `${item.label} (${item.files.length} parts)` : item.label} · {formatBytes(item.totalSize)}
                  </p>
                  {itemIsGguf(item) ? (
                    <div className="rounded-lg border border-theme-border bg-theme-bg px-3 py-2 text-sm text-theme-text">
                      llama-server (GGUF models)
                    </div>
                  ) : (
                    <select
                      value={(targetByKey[item.key] || '').split(':', 2)[1] || 'checkpoints'}
                      onChange={e => setTargetByKey(prev => ({ ...prev, [item.key]: `comfyui:${e.target.value}` }))}
                      className="w-full rounded-lg border border-theme-border bg-theme-bg px-3 py-2 text-sm text-theme-text focus:border-theme-accent focus:outline-none"
                    >
                      {COMFYUI_SUBDIRS.map(dir => (
                        <option key={dir} value={dir}>{dir}</option>
                      ))}
                    </select>
                  )}
                </div>
              ))}
            </div>

            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose} className="px-4 py-2 text-theme-text-muted hover:text-theme-text">
                Cancel
              </button>
              <button
                type="button"
                onClick={handlePull}
                disabled={pulling}
                className="flex items-center gap-2 rounded-lg bg-theme-accent px-4 py-2 text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {pulling && <Loader2 size={16} className="animate-spin" />}
                Pull {selectedItems.length === 1 ? 'model' : `${selectedItems.length} items`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
