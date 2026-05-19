import { useState, useEffect, useCallback, useRef } from 'react'

// Mock data for development/demo - gated behind VITE_USE_MOCK_DATA env var
const USE_MOCK_DATA = import.meta.env.VITE_USE_MOCK_DATA === 'true'

function getMockModels() {
  return [
    {
      id: 'Qwen/Qwen2.5-32B-Instruct-AWQ',
      name: 'Qwen2.5 32B AWQ',
      size: '15.7 GB',
      sizeGb: 15.7,
      vramRequired: 14,
      contextLength: 32768,
      specialty: 'General',
      description: 'High-quality general purpose, recommended for most users',
      tokensPerSec: 54,
      quantization: 'AWQ',
      status: 'loaded',
      fitsVram: true,
      fitsCurrentVram: false
    },
    {
      id: 'Qwen/Qwen2.5-7B-Instruct',
      name: 'Qwen2.5 7B',
      size: '4.2 GB',
      sizeGb: 4.2,
      vramRequired: 6,
      contextLength: 32768,
      specialty: 'Fast',
      description: 'Fast general-purpose model, good for simple tasks',
      tokensPerSec: 120,
      quantization: null,
      status: 'available',
      fitsVram: true,
      fitsCurrentVram: true
    },
    {
      id: 'Qwen/Qwen2.5-32B-Instruct-AWQ',
      name: 'Qwen2.5 Coder 32B AWQ',
      size: '15.7 GB',
      sizeGb: 15.7,
      vramRequired: 14,
      contextLength: 32768,
      specialty: 'Code',
      description: 'Optimized for coding tasks and technical work',
      tokensPerSec: 54,
      quantization: 'AWQ',
      status: 'downloaded',
      fitsVram: true,
      fitsCurrentVram: false
    },
    {
      id: 'Qwen/Qwen2.5-72B-Instruct-AWQ',
      name: 'Qwen2.5 72B AWQ',
      size: '35.0 GB',
      sizeGb: 35.0,
      vramRequired: 42,
      contextLength: 32768,
      specialty: 'Quality',
      description: 'Maximum quality, requires high-end GPU',
      tokensPerSec: 28,
      quantization: 'AWQ',
      status: 'available',
      fitsVram: false,
      fitsCurrentVram: false
    }
  ]
}

const MOCK_GPU = { vramTotal: 16, vramUsed: 13.2, vramFree: 2.8 }
const MOCK_CURRENT_MODEL = 'Qwen/Qwen2.5-32B-Instruct-AWQ'

// Named export for dev-only mocking (explicit opt-in via VITE_USE_MOCK_DATA)
export { getMockModels }

async function errorMessageFromResponse(response, fallback) {
  try {
    const data = await response.json()
    if (typeof data?.detail === 'string' && data.detail.trim()) return data.detail
    if (typeof data?.message === 'string' && data.message.trim()) return data.message
    if (typeof data?.error === 'string' && data.error.trim()) return data.error
  } catch {
    // Keep the stable fallback when the server returns non-JSON error content.
  }
  return fallback
}

export function useModels() {
  const [models, setModels] = useState(USE_MOCK_DATA ? getMockModels() : [])
  const [gpu, setGpu] = useState(USE_MOCK_DATA ? MOCK_GPU : null)
  const [currentModel, setCurrentModel] = useState(USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [configuredModel, setConfiguredModel] = useState(USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [recommendationAlternatives, setRecommendationAlternatives] = useState([])
  const [loading, setLoading] = useState(USE_MOCK_DATA ? false : true)
  const [error, setError] = useState(null)
  const [actionLoading, setActionLoading] = useState(null)
  const loadActiveRef = useRef(false)

  const fetchModels = useCallback(async () => {
    // If using mock data, don't attempt API call
    if (USE_MOCK_DATA) {
      setLoading(false)
      return
    }

    try {
      const response = await fetch('/api/models')
      if (!response.ok) throw new Error('Failed to fetch models')
      const data = await response.json()
      setModels(data.models)
      setGpu(data.gpu)
      setCurrentModel(data.currentModel)
      setConfiguredModel(data.configuredModel ?? null)
      setRecommendationAlternatives(data.recommendationAlternatives ?? [])
      setError(null)
    } catch (err) {
      setError(err.message)
      // No silent fallback - let error propagate to UI
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchModels()
    // Refresh every 30 seconds
    const interval = setInterval(fetchModels, 30000)
    return () => clearInterval(interval)
  }, [fetchModels])

  const downloadModel = async (modelId) => {
    setActionLoading(modelId)
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/download`, {
        method: 'POST'
      })
      if (!response.ok) throw new Error('Failed to start download')
      await fetchModels() // Refresh
    } catch (err) {
      setError(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  const loadModel = async (modelId) => {
    // Prevent concurrent activations — only one model can load at a time
    if (loadActiveRef.current) return
    loadActiveRef.current = true
    setActionLoading(modelId)
    setError(null)

    // Model activation is long-running (20-60s).  The browser connection
    // often drops before the backend responds (nginx 499 / NetworkError),
    // but the server still completes the activation.  Fire the POST
    // without blocking and poll for status instead.
    let serverError = null
    fetch(`/api/models/${encodeURIComponent(modelId)}/load`, { method: 'POST' })
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          const detail = body.detail || ''
          // 409/lock = another activation running — not a real error, keep polling
          if (/in progress|lock|already/i.test(detail)) return
          serverError = detail || 'Failed to load model'
        }
      })
      .catch(() => {}) // NetworkError — server still processing

    // Poll until model loads, server error, or timeout (2.5 min)
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 5000))
      if (serverError) {
        setError(serverError)
        break
      }
      try {
        const res = await fetch('/api/models')
        if (res.ok) {
          const data = await res.json()
          if (data.currentModel === modelId) {
            setModels(data.models)
            setGpu(data.gpu)
            setCurrentModel(data.currentModel)
            break
          }
        }
      } catch { /* poll failure, retry */ }
    }

    await fetchModels()
    setActionLoading(null)
    loadActiveRef.current = false
  }

  const deleteModel = async (modelId) => {
    if (!confirm(`Delete ${modelId}? This cannot be undone.`)) return
    
    setActionLoading(modelId)
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}`, {
        method: 'DELETE'
      })
      if (!response.ok) throw new Error('Failed to delete model')
      await fetchModels() // Refresh
    } catch (err) {
      setError(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  const benchmarkModel = async (modelId) => {
    setActionLoading(modelId)
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/benchmark`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_tokens: 128 })
      })
      if (!response.ok) throw new Error(await errorMessageFromResponse(response, 'Failed to benchmark model'))
      await fetchModels()
    } catch (err) {
      setError(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  return {
    models,
    gpu,
    currentModel,
    configuredModel,
    recommendationAlternatives,
    loading,
    error,
    actionLoading,
    downloadModel,
    loadModel,
    benchmarkModel,
    deleteModel,
    refresh: fetchModels
  }
}
