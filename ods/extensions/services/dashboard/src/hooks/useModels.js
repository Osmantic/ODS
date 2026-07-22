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
      id: 'Qwen/Qwen2.5-Coder-32B-Instruct-AWQ',
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
const MOCK_MODES = { odsMode: 'local', configuredMode: 'local' }
const DEFAULT_HERMES_MIN_CONTEXT = 65536
const DEFAULT_POLL_MS = 30000
const PENDING_MODEL_ACTION_POLL_MS = 2000
const MODELS_FETCH_TIMEOUT_MS = 30000
const MODEL_DOWNLOAD_START_TIMEOUT_MS = 15000
const MODEL_ACTIVATION_POLL_MS = 5000
// The dashboard API permits the host activation request to run for 600s.
// Keep the UI lock slightly longer so that deadline can settle before we fail.
const MODEL_ACTIVATION_TIMEOUT_MS = 610000
const ODS_MODES = new Set(['local', 'cloud', 'hybrid', 'lemonade'])
const LOCAL_MODEL_MODES = new Set(['local', 'hybrid', 'lemonade'])

// Named exports for dev-only mocking (explicit opt-in via VITE_USE_MOCK_DATA)
export { getMockModels, MOCK_MODES }

async function responseJson(response) {
  try {
    return await response.json()
  } catch {
    return {}
  }
}

function errorMessageFromPayload(data, fallback) {
  if (typeof data?.detail === 'string' && data.detail.trim()) return data.detail
  if (data?.detail && typeof data.detail === 'object') {
    if (typeof data.detail.message === 'string' && data.detail.message.trim()) return data.detail.message
    if (typeof data.detail.error === 'string' && data.detail.error.trim()) return data.detail.error
    if (typeof data.detail.detail === 'string' && data.detail.detail.trim()) return data.detail.detail
  }
  if (typeof data?.message === 'string' && data.message.trim()) return data.message
  if (typeof data?.error === 'string' && data.error.trim()) return data.error
  return fallback
}

async function errorMessageFromResponse(response, fallback) {
  return errorMessageFromPayload(await responseJson(response), fallback)
}

function conflictActiveModelId(data) {
  const nested = data?.detail && typeof data.detail === 'object'
    ? data.detail.activeModelId || data.detail.activeTarget
    : null
  for (const value of [data?.activeModelId, data?.activeTarget, nested]) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

function normalizeModelLifecycle(data) {
  if (!data || typeof data !== 'object') return null
  const operation = data.operation || data.activeOperation || null
  const target = data.target || data.activeTarget || null
  const modelId = data.modelId || data.activeModelId || target
  const active = data.active === true || data.lifecycleActive === true || Boolean(operation)
  if (!active || !operation) return null
  return {
    active: true,
    operation,
    target,
    modelId,
  }
}

function normalizeOdsMode(value) {
  const mode = typeof value === 'string' ? value.trim().toLowerCase() : ''
  return ODS_MODES.has(mode) ? mode : 'unknown'
}

function modelActivationModeError(effectiveMode, configuredMode) {
  if (effectiveMode === 'unknown' || configuredMode === 'unknown') {
    return 'ODS could not verify the active runtime mode. Repair or restart ODS before running a local model.'
  }
  if (effectiveMode !== configuredMode) {
    return `ODS is running in ${effectiveMode} mode but configured for ${configuredMode} mode. Restart or repair ODS before running a local model.`
  }
  if (!LOCAL_MODEL_MODES.has(effectiveMode)) {
    return 'ODS is running in cloud mode. A local-mode installation is required to run downloaded models.'
  }
  return null
}

export function useModels() {
  const [models, setModels] = useState(USE_MOCK_DATA ? getMockModels() : [])
  const [gpu, setGpu] = useState(USE_MOCK_DATA ? MOCK_GPU : null)
  const [currentModel, setCurrentModel] = useState(USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [configuredModel, setConfiguredModel] = useState(USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [modelLifecycle, setModelLifecycle] = useState(null)
  const [odsMode, setOdsMode] = useState(USE_MOCK_DATA ? MOCK_MODES.odsMode : 'unknown')
  const [configuredMode, setConfiguredMode] = useState(USE_MOCK_DATA ? MOCK_MODES.configuredMode : 'unknown')
  const [recommendationAlternatives, setRecommendationAlternatives] = useState([])
  const [hermesMinimumContext, setHermesMinimumContext] = useState(DEFAULT_HERMES_MIN_CONTEXT)
  const [loading, setLoading] = useState(USE_MOCK_DATA ? false : true)
  const [fetchError, setFetchError] = useState(null)
  const [mutationError, setMutationError] = useState(null)
  const [pendingActions, setPendingActionsState] = useState([])
  const pendingActionsRef = useRef([])
  const actionTokenRef = useRef(0)
  const modelsRequestRef = useRef(0)
  const latestSettledModelsRequestRef = useRef(0)
  const pollInFlightRef = useRef(false)
  const loadActiveRef = useRef(false)

  const updatePendingActions = useCallback((update) => {
    const nextActions = typeof update === 'function'
      ? update(pendingActionsRef.current)
      : update
    pendingActionsRef.current = nextActions
    setPendingActionsState(nextActions)
  }, [])

  const startAction = useCallback((modelId, kind) => {
    const action = { modelId, kind, token: ++actionTokenRef.current }
    updatePendingActions(actions => [...actions, action])
    return action
  }, [updatePendingActions])

  const finishAction = useCallback((token) => {
    updatePendingActions(actions => {
      const nextActions = actions.filter(action => action.token !== token)
      return nextActions.length === actions.length ? actions : nextActions
    })
  }, [updatePendingActions])

  const reconcilePendingActions = useCallback((nextModels) => {
    if (!Array.isArray(nextModels)) return

    updatePendingActions(actions => {
      const nextActions = actions.filter(action => {
        const model = nextModels.find(candidate => candidate.id === action.modelId)
        const downloadFinished = action.kind === 'download' &&
          (model?.status === 'downloaded' || model?.status === 'loaded')
        const deleteFinished = action.kind === 'delete' &&
          (!model || model.status === 'available')
        return !downloadFinished && !deleteFinished
      })
      return nextActions.length === actions.length ? actions : nextActions
    })
  }, [updatePendingActions])

  const fetchModels = useCallback(async () => {
    // If using mock data, don't attempt API call
    if (USE_MOCK_DATA) {
      setLoading(false)
      return
    }

    const requestId = ++modelsRequestRef.current
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), MODELS_FETCH_TIMEOUT_MS)
    try {
      const response = await fetch('/api/models', { signal: controller.signal })
      if (!response.ok) throw new Error('Failed to fetch models')
      const data = await response.json()

      // A slower, older request must not overwrite a newer snapshot.
      if (requestId < latestSettledModelsRequestRef.current) return data
      latestSettledModelsRequestRef.current = requestId

      setModels(data.models)
      setGpu(data.gpu)
      setCurrentModel(data.currentModel)
      setConfiguredModel(data.configuredModel ?? null)
      setModelLifecycle(normalizeModelLifecycle(data.modelLifecycle))
      const effectiveMode = normalizeOdsMode(data.odsMode)
      setOdsMode(effectiveMode)
      setConfiguredMode(normalizeOdsMode(data.configuredMode ?? data.odsMode))
      setRecommendationAlternatives(data.recommendationAlternatives ?? [])
      setHermesMinimumContext(Number(data.hermesMinimumContext || DEFAULT_HERMES_MIN_CONTEXT))
      setFetchError(null)
      reconcilePendingActions(data.models)
      return data
    } catch (err) {
      if (requestId >= latestSettledModelsRequestRef.current) {
        latestSettledModelsRequestRef.current = requestId
        setFetchError(err.message)
      }
      // No silent fallback - let error propagate to UI
    } finally {
      clearTimeout(timeout)
      setLoading(false)
    }
  }, [reconcilePendingActions])

  const pollModels = useCallback(async () => {
    if (document.hidden || pollInFlightRef.current) return
    pollInFlightRef.current = true
    try {
      await fetchModels()
    } finally {
      pollInFlightRef.current = false
    }
  }, [fetchModels])

  useEffect(() => {
    pollModels()
  }, [pollModels])

  const backendActivationModel = modelLifecycle?.operation === 'model_activation'
    ? modelLifecycle.modelId
    : null
  const backendLifecycleBusy = Boolean(modelLifecycle?.active)
  const pollInterval = pendingActions.some(action => action.kind === 'download' || action.kind === 'delete' || action.kind === 'load') || backendLifecycleBusy
    ? PENDING_MODEL_ACTION_POLL_MS
    : DEFAULT_POLL_MS

  useEffect(() => {
    // Poll promptly while a model mutation is pending, while keeping at most
    // one scheduled request in flight. Hidden tabs remain idle (#1490).
    const interval = setInterval(pollModels, pollInterval)

    // Resume immediately when the tab becomes visible again
    const onVisibility = () => { if (!document.hidden) pollModels() }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [pollInterval, pollModels])

  const downloadModel = async (modelId) => {
    const action = startAction(modelId, 'download')
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), MODEL_DOWNLOAD_START_TIMEOUT_MS)
    try {
      let response
      try {
        response = await fetch(`/api/models/${encodeURIComponent(modelId)}/download`, {
          method: 'POST',
          signal: controller.signal,
        })
      } catch (err) {
        if (err?.name === 'AbortError') {
          throw new Error(`Download for ${modelId} did not start within 15 seconds. Check the service and retry.`)
        }
        throw err
      } finally {
        clearTimeout(timeout)
      }

      if (!response.ok) {
        throw new Error(await errorMessageFromResponse(response, `Failed to start download for ${modelId}`))
      }
      await fetchModels() // Refresh
    } finally {
      clearTimeout(timeout)
      finishAction(action.token)
    }
  }

  const loadModel = async (modelId) => {
    const modeError = modelActivationModeError(odsMode, configuredMode)
    if (modeError) {
      setMutationError(modeError)
      return
    }

    // Prevent concurrent activations - only one model can load at a time.
    if (loadActiveRef.current) {
      const activeModelId = pendingActionsRef.current.find(action => action.kind === 'load')?.modelId
      setMutationError(activeModelId
        ? `Model activation is already in progress for ${activeModelId}.`
        : 'A model activation is already in progress.')
      return
    }
    loadActiveRef.current = true
    const action = startAction(modelId, 'load')
    setMutationError(null)

    // Model activation can consume the API's full 600-second budget. The
    // browser connection may still disappear while the server completes, so
    // status remains authoritative and the POST runs alongside polling.
    const controller = new AbortController()
    const startedAt = Date.now()
    let activationError = null
    let targetLoaded = false

    const activationRequest = fetch(`/api/models/${encodeURIComponent(modelId)}/load`, {
      method: 'POST',
      signal: controller.signal,
    })
      .then(async (response) => {
        if (response.ok) return

        const body = await responseJson(response)
        if (response.status === 409) {
          const activeModelId = conflictActiveModelId(body)
          if (activeModelId === modelId) return

          const detail = errorMessageFromPayload(body, 'Another model activation is in progress')
          activationError = activeModelId
            ? `${detail} Active target: ${activeModelId}; requested target: ${modelId}.`
            : `${detail} The server did not identify the active target, so this request cannot safely join it.`
          return
        }

        activationError = errorMessageFromPayload(body, 'Failed to load model')
      })
      // A dropped request does not prove activation failed. Continue polling
      // until the requested model appears or the explicit UI deadline expires.
      .catch(() => {})

    try {
      while (Date.now() - startedAt < MODEL_ACTIVATION_TIMEOUT_MS) {
        const remainingMs = MODEL_ACTIVATION_TIMEOUT_MS - (Date.now() - startedAt)
        await new Promise(resolve => setTimeout(resolve, Math.min(MODEL_ACTIVATION_POLL_MS, remainingMs)))

        if (activationError) break
        const data = await fetchModels()
        if (data?.currentModel === modelId) {
          targetLoaded = true
          break
        }
      }

      // Take one final authoritative snapshot at the deadline or after a POST
      // failure. This cannot turn an unverified 409 into same-target success.
      const finalData = await fetchModels()
      if (!activationError && finalData?.currentModel === modelId) targetLoaded = true

      if (!targetLoaded) {
        setMutationError(activationError ||
          `Timed out after 10 minutes waiting for ${modelId} to activate. The server may still be finishing; refresh before retrying.`)
      }
    } finally {
      controller.abort()
      void activationRequest
      finishAction(action.token)
      loadActiveRef.current = false
    }
  }

  const deleteModel = async (modelId) => {
    setMutationError(null)
    const action = startAction(modelId, 'delete')
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}`, {
        method: 'DELETE'
      })
      if (!response.ok) {
        throw new Error(await errorMessageFromResponse(response, `Failed to delete ${modelId}`))
      }
      await fetchModels() // Refresh
    } catch (err) {
      setMutationError(err.message)
    } finally {
      finishAction(action.token)
    }
  }

  const benchmarkModel = async (modelId) => {
    setMutationError(null)
    const action = startAction(modelId, 'benchmark')
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/benchmark`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_tokens: 128 })
      })
      if (!response.ok) throw new Error(await errorMessageFromResponse(response, 'Failed to benchmark model'))
      await fetchModels()
    } catch (err) {
      setMutationError(err.message)
    } finally {
      finishAction(action.token)
    }
  }

  const activationAction = pendingActions.find(action => action.kind === 'load')
  const latestAction = pendingActions[pendingActions.length - 1]
  const activationLoading = activationAction?.modelId ?? backendActivationModel ?? null
  const actionLoading = activationAction?.modelId ?? latestAction?.modelId ?? null
  const actionLoadingModels = [
    ...new Set([
      ...pendingActions.map(action => action.modelId),
      backendActivationModel,
    ].filter(Boolean)),
  ]
  const error = mutationError || fetchError
  const activationModeError = modelActivationModeError(odsMode, configuredMode)

  return {
    models,
    gpu,
    currentModel,
    configuredModel,
    modelLifecycle,
    odsMode,
    configuredMode,
    canActivateModels: activationModeError === null,
    activationModeError,
    recommendationAlternatives,
    hermesMinimumContext,
    loading,
    error,
    actionLoading,
    actionLoadingModels,
    activationLoading,
    downloadModel,
    loadModel,
    benchmarkModel,
    deleteModel,
    refresh: fetchModels
  }
}
