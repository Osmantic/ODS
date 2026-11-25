import {modelActivationStatus} from '../lib/modelActivationStatus'
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
const DEFAULT_PIXEL_MIN_CONTEXT = 16384
const DEFAULT_POLL_MS = 30000
const PENDING_MODEL_ACTION_POLL_MS = 2000
const MODELS_FETCH_TIMEOUT_MS = 30000
const MODEL_DOWNLOAD_START_TIMEOUT_MS = 15000
const MODEL_ACTIVATION_POLL_MS = 5000
// Delete allows 30s at the host; the 128-token benchmark allows 384s.
const MODEL_DELETE_TIMEOUT_MS = 35000
const MODEL_BENCHMARK_TIMEOUT_MS = 400000
const MODEL_RUNTIME_TIMEOUT_MS = 1225000
// Activation allows 2700s plus 120s of download-busy retry grace.
// Keep the UI lock until that budget and a small response margin have elapsed.
const MODEL_ACTIVATION_TIMEOUT_MS = 2825000
const ODS_MODES = new Set(['local', 'cloud', 'hybrid'])
const LOCAL_MODEL_MODES = new Set(['local', 'hybrid'])

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

class ModelActionError extends Error {
  constructor(message, deletionDetail = null) {
    super(message)
    // Only a typed, committed deletion outcome authorizes immediate readback.
    const committedOutcome = deletionDetail?.code === 'model_delete_cleanup_pending' ||
      deletionDetail?.code === 'model_delete_status_update_failed'
    this.deletionCommitted = committedOutcome && deletionDetail.deletionCommitted === true
  }
}

async function modelActionRequest(url, options, budget, timeoutMessage, failureMessage, expectedStatus = null) {
  const controller = new AbortController()
  let timer
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(timeoutMessage))
      controller.abort()
    }, budget)
  })
  try {
    await Promise.race([
      (async () => {
        const response = await fetch(url, { ...options, signal: controller.signal })
        if (!response.ok) {
          const data = await responseJson(response)
          throw new ModelActionError(
            errorMessageFromPayload(data, failureMessage),
            options.method === 'DELETE' ? data?.detail : null,
          )
        }
        if (expectedStatus && (await responseJson(response)).status !== expectedStatus) {
          throw new Error('The runtime operation was not confirmed. Refresh its status before retrying.')
        }
      })(),
      deadline,
    ])
  } finally {
    clearTimeout(timer)
    controller.abort()
  }
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

function hasActiveModelActivation(data) {
  const lifecycle = normalizeModelLifecycle(data?.modelLifecycle)
  return lifecycle?.operation === 'model_activation'
}

function normalizeOdsMode(value) {
  const mode = typeof value === 'string' ? value.trim().toLowerCase() : ''
  return ODS_MODES.has(mode) ? mode : 'unknown'
}

const START_REFUSALS = new Set(['memory_refused', 'gpu_busy'])

function normalizeActiveRuntime(value) {
  // Models needs only display identity and context; route custody and provider
  // capabilities remain validated by the server, outside this display state.
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const { source, model, contextLength } = value
  if (!['local-switchboard', 'external-host', 'remote-provider'].includes(source)
    || typeof model !== 'string' || !model.trim() || model.length > 256) return null
  if (contextLength != null && (!Number.isInteger(contextLength) || contextLength < 1 || contextLength > 10000000)) return null
  if (source === 'remote-provider' && (!Number.isInteger(contextLength) || contextLength < 4096
    || !Number.isInteger(value.maxTokens) || value.maxTokens < 1 || value.maxTokens > contextLength
    || typeof value.reasoning !== 'boolean')) return null
  return { source, model, contextLength: contextLength ?? null }
}

function normalizeModelManagement(value) {
  const managed = typeof value?.managed === 'boolean' ? value.managed : null
  const running = managed === true && value.running === true
  // Why a managed Windows model is not running, in the launcher's words; a
  // memory refusal makes Resume start the model anyway.
  const runtimeError = managed === true && !running && typeof value.runtimeError === 'string' ? value.runtimeError : ''
  return {
    managed,
    canActivate: managed === true && value.canActivate === true,
    canUnload: managed === true && value.canUnload === true,
    running,
    reason: typeof value?.reason === 'string' ? value.reason : '',
    runtimeError,
    startRefusal: runtimeError && START_REFUSALS.has(value.startRefusal) ? value.startRefusal : '',
  }
}

function modelActivationModeError(effectiveMode, configuredMode, llmBackend, hostRuntime, management, externalApi = null, activeRuntime = null) {
  if (llmBackend === 'external') {
    const via = externalApi?.host ? ` at ${externalApi.host}` : ''
    return `ODS uses a model API${via}. Models downloaded here stay on this computer but are not used while API mode is on; to run them, rerun the ODS installer and leave API mode.`
  }
  // A model on the Windows host changes only through the host agent, and
  // only when it proved the server is the one ODS runs for this install.
  if (hostRuntime && !(management?.managed && management.canActivate)) {
    return management?.managed === false
      ? 'The model server on this computer is not managed by this ODS installation. Change the model in that server, or rerun the ODS installer to manage it here.'
      : management?.reason || 'Model management is temporarily unavailable. Refresh the runtime status.'
  }
  if (activeRuntime?.source === 'remote-provider') {
    return 'Switch back to a local model in Model connections to use local model activation controls.'
  }
  if (effectiveMode === 'unknown' || configuredMode === 'unknown') {
    return 'ODS could not verify the active runtime mode. Repair or restart ODS before running a local model.'
  }
  if (effectiveMode !== configuredMode) {
    return `ODS is running in ${effectiveMode} mode but configured for ${configuredMode} mode. Restart or repair ODS before running a local model.`
  }
  if (!LOCAL_MODEL_MODES.has(effectiveMode)) {
    return 'ODS is in cloud mode, so chat uses a model API. Downloaded models run only in local mode; if you connected that API in Settings > Remote model, switch back to the local model there.'
  }
  return null
}

function waitForActivationPoll(delay, signal, wake = null) {
  if (signal.aborted) return Promise.resolve()
  return new Promise(resolve => {
    const finish = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', finish)
      resolve()
    }
    const timer = setTimeout(finish, delay)
    signal.addEventListener('abort', finish, { once: true })
    // A settled activation request ends the wait early so the confirming
    // status read is not held behind the regular poll interval.
    if (wake) wake.then(finish, finish)
  })
}

// One /api/models answer as the hook's state.
function catalogState(data) {
  return {
    models: data.models,
    gpu: data.gpu,
    currentModel: data.currentModel,
    loadedModel: data.loadedModel ?? null,
    activationReadyModel: data.activationReadyModel ?? null,
    configuredModel: data.configuredModel ?? null,
    modelLifecycle: normalizeModelLifecycle(data.modelLifecycle),
    modelActivation: modelActivationStatus(data.modelActivation),
    modelRecoveryPending: data.modelRecoveryPending === true,
    odsMode: normalizeOdsMode(data.odsMode),
    configuredMode: normalizeOdsMode(data.configuredMode ?? data.odsMode),
    llmBackend: typeof data.llmBackend === 'string' ? data.llmBackend.trim().toLowerCase() : 'unknown',
    // API mode: the model and host serving chat (never the key).
    externalApi: String(data.llmBackend || '').trim().toLowerCase() === 'external'
      ? {
          model: typeof data.externalModel === 'string' && data.externalModel ? data.externalModel : null,
          host: typeof data.externalHost === 'string' && data.externalHost ? data.externalHost : null,
        }
      : null,
    hostRuntime: data.hostRuntime === true,
    activeRuntime: normalizeActiveRuntime(data.activeRuntime),
    modelManagement: normalizeModelManagement(data.modelManagement),
    recommendationAlternatives: data.recommendationAlternatives ?? [],
    hermesMinimumContext: Number(data.hermesMinimumContext || DEFAULT_HERMES_MIN_CONTEXT),
    pixelMinimumContext: Number(data.pixelMinimumContext || DEFAULT_PIXEL_MIN_CONTEXT),
  }
}

// The last catalog this page load read. The catalog can take seconds to
// answer, so a Models page opened again starts from it instead of an empty
// skeleton, holding its actions until its own first read lands.
let lastCatalog = null

export function forgetLastCatalog() {
  lastCatalog = null
}

export function useModels({observe=true, reuseLastCatalog=false} = {}) {
  // Never activation readback: confirmation waits for this mount's own reads.
  const [seed] = useState(() => (reuseLastCatalog && observe && !USE_MOCK_DATA && lastCatalog
    ? catalogState(lastCatalog) : null))
  const [models, setModels] = useState(seed ? seed.models : USE_MOCK_DATA ? getMockModels() : [])
  const [gpu, setGpu] = useState(seed ? seed.gpu : USE_MOCK_DATA ? MOCK_GPU : null)
  const [currentModel, setCurrentModel] = useState(seed ? seed.currentModel : USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [loadedModel, setLoadedModel] = useState(seed ? seed.loadedModel : USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [activationReadyModel, setActivationReadyModel] = useState(seed ? seed.activationReadyModel : USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [configuredModel, setConfiguredModel] = useState(seed ? seed.configuredModel : USE_MOCK_DATA ? MOCK_CURRENT_MODEL : null)
  const [modelLifecycle, setModelLifecycle] = useState(seed ? seed.modelLifecycle : null)
  const [modelActivation, setModelActivation] = useState(seed ? seed.modelActivation : null)
  const [modelRecoveryPending, setModelRecoveryPending] = useState(seed ? seed.modelRecoveryPending : false)
  const [odsMode, setOdsMode] = useState(seed ? seed.odsMode : USE_MOCK_DATA ? MOCK_MODES.odsMode : 'unknown')
  const [configuredMode, setConfiguredMode] = useState(seed ? seed.configuredMode : USE_MOCK_DATA ? MOCK_MODES.configuredMode : 'unknown')
  const [llmBackend, setLlmBackend] = useState(seed ? seed.llmBackend : USE_MOCK_DATA ? 'llama-server' : 'unknown')
  const [hostRuntime, setHostRuntime] = useState(seed ? seed.hostRuntime : false)
  const [externalApi, setExternalApi] = useState(seed ? seed.externalApi : null)
  const [activeRuntime, setActiveRuntime] = useState(seed ? seed.activeRuntime : null)
  const [modelManagement, setModelManagement] = useState(() => (seed ? seed.modelManagement : normalizeModelManagement(null)))
  const [runtimeActionLoading, setRuntimeActionLoading] = useState(null)
  const [recommendationAlternatives, setRecommendationAlternatives] = useState(seed ? seed.recommendationAlternatives : [])
  const [hermesMinimumContext, setHermesMinimumContext] = useState(seed ? seed.hermesMinimumContext : DEFAULT_HERMES_MIN_CONTEXT)
  const [pixelMinimumContext, setPixelMinimumContext] = useState(seed ? seed.pixelMinimumContext : DEFAULT_PIXEL_MIN_CONTEXT)
  const [loading, setLoading] = useState(USE_MOCK_DATA ? false : !seed)
  const [catalogConfirmed, setCatalogConfirmed] = useState(!seed)
  const [fetchError, setFetchError] = useState(null)
  const [mutationError, setMutationError] = useState(null)
  const clearMutationError = useCallback(() => setMutationError(null), [])
  const [pendingActions, setPendingActionsState] = useState([])
  const pendingActionsRef = useRef([])
  const actionTokenRef = useRef(0)
  const modelsRequestRef = useRef(0)
  const latestSettledModelsRequestRef = useRef(0)
  const latestModelsSnapshotRef = useRef(null)
  const pollInFlightRef = useRef(false)
  const loadActiveRef = useRef(false)
  const activationControllerRef = useRef(null)

  useEffect(() => () => {
    // Navigation ends this page's observation. The host still owns any
    // accepted activation, and a new page reads its lifecycle on mount.
    activationControllerRef.current?.abort()
  }, [])

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

  const fetchModels = useCallback(async ({ signal } = {}) => {
    if (signal?.aborted) return null
    // If using mock data, don't attempt API call
    if (USE_MOCK_DATA) {
      setLoading(false)
      return
    }

    const requestId = ++modelsRequestRef.current
    const controller = new AbortController()
    const cancel = () => controller.abort()
    signal?.addEventListener('abort', cancel, { once: true })
    let timedOut = false
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, MODELS_FETCH_TIMEOUT_MS)
    try {
      const response = await fetch('/api/models', { signal: controller.signal })
      if (!response.ok) throw new Error('Failed to fetch models')
      const data = await response.json()
      if (signal?.aborted) return null

      // Keep publishing ordered, but return valid readback to its caller.
      // Faster background polls must not starve activation confirmation.
      if (requestId < latestSettledModelsRequestRef.current) return data
      latestSettledModelsRequestRef.current = requestId
      latestModelsSnapshotRef.current = data
      lastCatalog = data

      const next = catalogState(data)
      setModels(next.models)
      setGpu(next.gpu)
      setCurrentModel(next.currentModel)
      setLoadedModel(next.loadedModel)
      setActivationReadyModel(next.activationReadyModel)
      setConfiguredModel(next.configuredModel)
      setModelLifecycle(next.modelLifecycle)
      setModelActivation(next.modelActivation)
      setModelRecoveryPending(next.modelRecoveryPending)
      setOdsMode(next.odsMode)
      setConfiguredMode(next.configuredMode)
      setLlmBackend(next.llmBackend)
      setExternalApi(next.externalApi)
      setHostRuntime(next.hostRuntime)
      setActiveRuntime(next.activeRuntime)
      setModelManagement(next.modelManagement)
      setRecommendationAlternatives(next.recommendationAlternatives)
      setHermesMinimumContext(next.hermesMinimumContext)
      setPixelMinimumContext(next.pixelMinimumContext)
      setFetchError(null)
      setCatalogConfirmed(true)
      reconcilePendingActions(data.models)
      return data
    } catch (err) {
      if (signal?.aborted) return null
      if (requestId >= latestSettledModelsRequestRef.current) {
        latestSettledModelsRequestRef.current = requestId
        setFetchError(timedOut
          ? 'Could not refresh model status within 30 seconds. ODS may still be working; wait for status confirmation before retrying a model change.'
          : err.message)
        setModelActivation(null)
        setActiveRuntime(null)
        setModelManagement(normalizeModelManagement(null))
      }
      // No silent fallback - let error propagate to UI
    } finally {
      clearTimeout(timeout)
      signal?.removeEventListener('abort', cancel)
      if (!signal?.aborted) setLoading(false)
    }
  }, [reconcilePendingActions])

  // A caller may hide the local catalog, but accepted mutations still need
  // their own readback. Explicit activation confirmation also keeps fetching.
  const observing = observe || pendingActions.length > 0 || Boolean(runtimeActionLoading)
  const pollModels = useCallback(async () => {
    if (!observing || document.hidden || pollInFlightRef.current) return
    pollInFlightRef.current = true
    try {
      await fetchModels()
    } finally {
      pollInFlightRef.current = false
    }
  }, [fetchModels,observing])

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
    if (!observing) return
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
  }, [pollInterval, pollModels, observing])

  const downloadModel = async (modelId) => {
    const action = startAction(modelId, 'download')
    try {
      await modelActionRequest(
        '/api/models/' + encodeURIComponent(modelId) + '/download',
        { method: 'POST' }, MODEL_DOWNLOAD_START_TIMEOUT_MS,
        'Download for ' + modelId + ' did not start within 15 seconds. The server may still be working; refresh before retrying.',
        'Failed to start download for ' + modelId,
      )
      await fetchModels() // Refresh
    } finally {
      finishAction(action.token)
    }
  }

  const loadModel = async (modelId, options = {}) => {
    const modeError = modelActivationModeError(odsMode, configuredMode, llmBackend, hostRuntime, modelManagement, externalApi, activeRuntime)
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
    // Invalidate catalog requests started before this mutation. Their replies
    // describe the previous route even if no newer poll has settled yet.
    latestSettledModelsRequestRef.current = ++modelsRequestRef.current
    const action = startAction(modelId, 'load')
    setMutationError(null)

    // Model activation can consume the host's 45-minute budget plus retry grace. The
    // browser connection may still disappear while the server completes, so
    // status remains authoritative and the POST runs alongside polling.
    const controller = new AbortController()
    const startedAt = Date.now()
    activationControllerRef.current = controller
    let activationError = null
    let targetLoaded = false
    const requestedContextLength = Number(options.contextLength || 0) || null
    const snapshotMatches = (data) => {
      if (
        data?.currentModel !== modelId ||
        data?.activationReadyModel !== modelId ||
        hasActiveModelActivation(data)
      ) return false
      if (!requestedContextLength) return true
      const activeModel = data?.models?.find(model => model.id === modelId)
      return Number(activeModel?.contextLength || 0) === requestedContextLength
    }
    // A superseded response may confirm the operation when the newer published
    // inventory agrees. A newer route/context or active lifecycle still blocks it.
    const activationMatches = data => snapshotMatches(data) && snapshotMatches(latestModelsSnapshotRef.current)

    const activationRequestOptions = {
      method: 'POST',
      signal: controller.signal,
    }
    if (requestedContextLength) {
      activationRequestOptions.headers = { 'Content-Type': 'application/json' }
      activationRequestOptions.body = JSON.stringify({
        context_length: requestedContextLength,
      })
    }
    // The server answers the POST only after the model and its consumers are
    // committed. Confirm right away instead of waiting out the poll interval;
    // a joined in-flight activation or a dropped connection keeps polling.
    let activationAnswered = false
    // Set when no answer to this page's request will come: a 409 joined the
    // activation another request is running, or the connection dropped.
    // Status reads decide then, and a failed activation ends its lifecycle
    // without the model.
    let statusOnly = false
    let idleReads = 0
    let wakeActivationPoll = () => {}
    let activationWake = new Promise(resolve => { wakeActivationPoll = resolve })
    const activationRequest = fetch(`/api/models/${encodeURIComponent(modelId)}/load`, activationRequestOptions)
      .then(async (response) => {
        if (response.ok) {
          activationAnswered = true
          return
        }

        const body = await responseJson(response)
        if (response.status === 409) {
          if (body?.detail?.code === 'runtime_architecture_unsupported') {
            activationError = errorMessageFromPayload(body, 'This runtime does not support the model architecture.')
            return
          }
          if (body?.detail?.code === 'pixel_chat_active') {
            activationError = errorMessageFromPayload(
              body,
              'Portal is working. Stop the active response before changing models.'
            )
            return
          }
          const activeModelId = conflictActiveModelId(body)
          if (activeModelId === modelId && !requestedContextLength) {
            statusOnly = true
            return
          }

          const detail = errorMessageFromPayload(body, 'Another model activation is in progress')
          activationError = activeModelId
            ? `${detail} Active target: ${activeModelId}; requested target: ${modelId}.`
            : `${detail} The server did not identify the active target, so this request cannot safely join it.`
          return
        }

        activationError = errorMessageFromPayload(body, 'Failed to load model')
      })
      // A dropped request does not prove activation failed. Continue polling
      // until the requested model appears, the activation visibly ends, or
      // the explicit UI deadline expires.
      .catch(() => { statusOnly = true })
      .then(() => {
        if (activationAnswered || activationError) {
          activationAnswered = true
          wakeActivationPoll()
        }
      })

    try {
      while (!controller.signal.aborted && Date.now() - startedAt < MODEL_ACTIVATION_TIMEOUT_MS) {
        const remainingMs = MODEL_ACTIVATION_TIMEOUT_MS - (Date.now() - startedAt)
        await waitForActivationPoll(Math.min(MODEL_ACTIVATION_POLL_MS, remainingMs), controller.signal, activationWake)
        // Only the first wait after the server answered is cut short.
        if (activationAnswered) activationWake = null
        if (controller.signal.aborted) return

        if (activationError) break
        const data = await fetchModels({ signal: controller.signal })
        if (controller.signal.aborted) return
        if (activationMatches(data)) {
          targetLoaded = true
          break
        }
        // Two reads in a row with no model operation running and the model
        // not running mean the activation ended without it. Waiting out the
        // deadline cannot change that.
        if (statusOnly && data && !normalizeModelLifecycle(data.modelLifecycle) && data.currentModel !== modelId) {
          idleReads += 1
          if (idleReads >= 2) {
            activationError = `The activation of ${modelId} ended without loading it. Refresh the model list, then try again.`
            break
          }
        } else {
          idleReads = 0
        }
      }

      // Take one final authoritative snapshot at the deadline or after a POST
      // failure. This cannot turn an unverified 409 into same-target success.
      if (controller.signal.aborted) return
      const finalData = await fetchModels({ signal: controller.signal })
      if (controller.signal.aborted) return
      const confirmed = !activationError && activationMatches(finalData)

      if (!confirmed) {
        setMutationError(activationError || (targetLoaded
          ? `Could not confirm activation of ${modelId} against the latest model status. Refresh before retrying.`
          : `Timed out after 47 minutes waiting for ${modelId} to activate. The server may still be finishing; refresh before retrying.`))
      }
    } finally {
      if (!controller.signal.aborted) finishAction(action.token)
      controller.abort()
      activationControllerRef.current = null
      void activationRequest
      // A background poll started before final confirmation must not restore
      // the old selection after the selector has released its switching state.
      latestSettledModelsRequestRef.current = ++modelsRequestRef.current
      loadActiveRef.current = false
    }
  }

  const changeRuntime = async (operation) => {
    if (!modelManagement.managed || !modelManagement.canUnload) {
      setMutationError(modelManagement.reason || 'Runtime controls are unavailable for this installation.')
      return
    }
    if (loadActiveRef.current || backendLifecycleBusy || pendingActionsRef.current.length) {
      setMutationError('Wait for the current model operation to finish before changing the runtime.')
      return
    }
    loadActiveRef.current = true
    setRuntimeActionLoading(operation)
    setMutationError(null)
    latestSettledModelsRequestRef.current = ++modelsRequestRef.current
    try {
      await modelActionRequest(
        '/api/models/runtime/' + operation,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
        MODEL_RUNTIME_TIMEOUT_MS,
        'The runtime operation timed out. The server may still be working; refresh before retrying.',
        'Could not ' + operation + ' the model runtime.',
        operation === 'stop' ? 'stopped' : 'started',
      )
    } catch (err) {
      setMutationError(err.message)
    } finally {
      await fetchModels()
      loadActiveRef.current = false
      setRuntimeActionLoading(null)
    }
  }

  const deleteModel = async (modelId) => {
    setMutationError(null)
    const action = startAction(modelId, 'delete')
    try {
      await modelActionRequest(
        '/api/models/' + encodeURIComponent(modelId),
        { method: 'DELETE' }, MODEL_DELETE_TIMEOUT_MS,
        'Delete for ' + modelId + ' timed out. The server may still be working; refresh before retrying.',
        'Failed to delete ' + modelId,
      )
      await fetchModels() // Refresh
    } catch (err) {
      setMutationError(err.message)
      if (err instanceof ModelActionError && err.deletionCommitted) await fetchModels()
    } finally {
      finishAction(action.token)
    }
  }

  const benchmarkModel = async (modelId) => {
    setMutationError(null)
    const action = startAction(modelId, 'benchmark')
    try {
      await modelActionRequest(
        '/api/models/' + encodeURIComponent(modelId) + '/benchmark',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ max_tokens: 128 }),
        }, MODEL_BENCHMARK_TIMEOUT_MS,
        'Benchmark for ' + modelId + ' timed out. The server may still be working; refresh before retrying.',
        'Failed to benchmark model',
      )
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
  const activationModeError = modelActivationModeError(odsMode, configuredMode, llmBackend, hostRuntime, modelManagement, externalApi, activeRuntime)

  return {
    models,
    gpu,
    currentModel,
    loadedModel,
    activationReadyModel,
    configuredModel,
    modelLifecycle,
    modelActivation,
    modelRecoveryPending,
    odsMode,
    configuredMode,
    llmBackend,
    hostRuntime,
    externalApi,
    activeRuntime,
    modelManagement,
    runtimeActionLoading,
    stopRuntime: () => changeRuntime('stop'),
    startRuntime: () => changeRuntime('start'),
    canActivateModels: activationModeError === null,
    clearMutationError,
    activationModeError,
    recommendationAlternatives,
    hermesMinimumContext,
    pixelMinimumContext,
    loading,
    catalogConfirmed,
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
