import {useCallback, useEffect, useRef, useState} from 'react'
import {Loader2, RefreshCw, Wrench} from 'lucide-react'
import HelpLink from '../HelpLink'

const THINKING_TEXT = {
  enable_thinking: 'Thinking can be turned off',
  always: 'Always thinks before it answers',
  none: 'Answers without a thinking step',
}

// The API allows 180 seconds for the host probe; include response transit.
const RECHECK_TIMEOUT_MS = 195000

async function readJson(response) {
  try { return await response.json() } catch { return null }
}

function errorText(body, fallback) {
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  return detail?.message || detail?.error || fallback
}

function chips(summary, templateOverride) {
  const list = []
  if (templateOverride?.active) list.push({tone: 'neutral', text: 'Uses a fixed chat template'})
  if (summary.chat === true) list.push({tone: 'ok', text: 'Answers chat'})
  if (summary.chat === false) list.push({tone: 'warn', text: 'Did not answer the chat check'})
  if (summary.tools === true) list.push({tone: 'ok', text: 'Tool-call check passed'})
  if (summary.tools === false) list.push({tone: 'warn', text: 'Tool-call check did not pass'})
  list.push(summary.toolsStreamed === true
    ? {tone: 'ok', text: 'Streaming tool check passed'}
    : summary.toolsStreamed === false
      ? {tone: 'warn', text: 'Streaming tool check did not pass'}
      : {tone: 'neutral', text: 'Streaming tool check not verified'})
  const thinking = THINKING_TEXT[summary.thinking?.control]
  if (thinking) {
    const works = summary.thinking?.works
    list.push(works === true
      ? {tone: 'neutral', text: thinking}
      : {tone: works === false ? 'warn' : 'neutral', text: works === false ? 'Thinking check did not pass' : 'Thinking not verified'})
  }
  if (summary.vision === true) list.push({tone: 'ok', text: 'Sees images'})
  if (summary.vision === false) list.push({tone: 'warn', text: 'Image input did not work'})
  if (typeof summary.tokensPerSecond === 'number') list.push({tone: 'neutral', text: `About ${Math.round(summary.tokensPerSecond)} tokens/s`})
  return list
}

const TONES = {
  ok: 'border-emerald-300/25 bg-emerald-400/10 text-emerald-200',
  warn: 'border-amber-300/25 bg-amber-400/10 text-amber-100',
  neutral: 'border-theme-border bg-theme-text-secondary/10 text-theme-text-secondary',
}

/** Advisory: what the running model was measured to do on this machine. */
export default function ModelProfileSummary({modelId, lifecycleActive = false}) {
  return <ProfileForModel key={modelId} modelId={modelId} lifecycleActive={lifecycleActive}/>
}

function ProfileForModel({modelId, lifecycleActive}) {
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)
  const [rechecking, setRechecking] = useState(false)
  const [applying, setApplying] = useState(false)
  const [notice, setNotice] = useState('')
  const readScope = useRef({active: false, serial: 0, controller: null})
  const recheckRequest = useRef(null)

  const load = useCallback(async ({preserveOnError = false} = {}) => {
    const scope = readScope.current
    if (!scope.active) return
    scope.controller?.abort()
    const controller = new AbortController()
    scope.controller = controller
    const serial = ++scope.serial
    const current = () => scope.active && scope.serial === serial
    let timer
    try {
      const body = await Promise.race([
        (async () => {
          const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/profile`, {cache: 'no-store', signal: controller.signal})
          const value = await readJson(response)
          if (!response.ok || !value || typeof value.mode !== 'string' || value.modelId !== modelId
            || (value.profile?.modelId && value.profile.modelId !== modelId)) throw new Error('unavailable')
          return value
        })(),
        new Promise((_, reject) => {
          timer = setTimeout(() => { controller.abort(); reject(new Error('unavailable')) }, 15000)
        }),
      ])
      if (!current()) return
      setData(body)
      setFailed(false)
      return true
    } catch {
      if (current() && !preserveOnError) setFailed(true)
      return false
    } finally {
      clearTimeout(timer)
      if (scope.controller === controller) scope.controller = null
    }
  }, [modelId])

  useEffect(() => {
    const scope = readScope.current
    scope.active = Boolean(modelId) && !lifecycleActive
    // First activation records its profile before the lifecycle settles.
    // Re-read that result, never start a new probe battery automatically.
    if (scope.active) load()
    return () => {
      scope.active = false
      scope.serial++
      scope.controller?.abort()
    }
  }, [modelId, lifecycleActive, load])

  useEffect(() => () => {
    const request = recheckRequest.current
    recheckRequest.current = null
    clearTimeout(request?.timer)
    request?.controller.abort()
  }, [])

  const recheck = async () => {
    if (recheckRequest.current) return
    const request = {controller: new AbortController(), timer: null}
    recheckRequest.current = request
    setRechecking(true)
    setNotice('')
    try {
      const {response, body} = await Promise.race([
        (async () => {
          const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/profile/recheck`, {
            method: 'POST', signal: request.controller.signal,
          })
          return {response, body: await readJson(response)}
        })(),
        new Promise((_, reject) => {
          request.timer = setTimeout(() => {
            request.controller.abort()
            reject(new Error('Recheck response unavailable'))
          }, RECHECK_TIMEOUT_MS)
        }),
      ])
      if (recheckRequest.current !== request) return
      if (!response.ok) {
        setNotice(errorText(body, 'The check could not run. Try again in a minute.'))
      } else if (!body) {
        throw new Error('Recheck response unavailable')
      } else if (body?.status === 'error') {
        setNotice('The check did not finish. Chat still works; try again later.')
      }
    } catch {
      if (recheckRequest.current === request) {
        setNotice('The check did not return a result. The host may still be checking. Check the model status before trying again.')
      }
    } finally {
      clearTimeout(request.timer)
      if (recheckRequest.current === request) {
        const refreshed = await load({preserveOnError: true})
        if (recheckRequest.current === request) {
          if (!refreshed) setNotice(previous => previous || 'The saved check could not refresh. Previous results are still shown; reload the page to read the latest result.')
          recheckRequest.current = null
          setRechecking(false)
        }
      }
    }
  }

  // ODS ships a fixed chat template for a model whose own template is known
  // to be broken; the server offers it only on an exact match (any-model WP5).
  const tryFixedTemplate = async () => {
    setApplying(true)
    setNotice('')
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/chat-template`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({override: data.templateOverride.id}),
      })
      if (!response.ok) {
        setNotice(errorText(await readJson(response), 'The model could not restart with the fixed template. Check the model status, then try again.'))
      }
    } catch {
      setNotice('The restart did not answer in time. Check the model status before trying again.')
    } finally {
      await load()
      setApplying(false)
    }
  }

  if (!modelId || lifecycleActive || failed || !data || data.mode === 'off') return null
  const recheckButton = (
    <button
      type="button"
      onClick={recheck}
      disabled={rechecking || applying}
      className="inline-flex items-center gap-1 rounded border border-theme-border px-2 py-0.5 text-theme-text-secondary hover:text-theme-text disabled:opacity-50"
    >
      {rechecking ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
      {rechecking ? 'Checking…' : 'Check again'}
    </button>
  )
  const profile = data.profile
  if (!profile) {
    // A model running since before checks existed has no profile yet. The
    // Portal advisory sends the owner here to press Check again.
    return (
      <div className="mt-2 space-y-1.5 text-[11px] text-theme-text-muted">
        <p>No saved capability check is available for this model on this machine. Choose Check again to run one.</p>
        {recheckButton}
        {notice && <p role="status" className="text-amber-200">{notice}</p>}
      </div>
    )
  }
  const summary = profile.result?.summary || {}
  const facts = profile.result?.facts || {}
  const offer = data.templateOverride
  const showOffer = Boolean(offer && !offer.active)
  const list = chips(summary, offer)
  const hasWarning = list.some(chip => chip.tone === 'warn')
  const checkedAt = Date.parse(profile.recordedAt || '')
  return (
    <section aria-label="What this model can do" className="mt-3 space-y-2 text-[11px]">
      <div className="flex flex-wrap gap-1.5">
        {list.map(chip => (
          <span key={chip.text} className={`rounded-md border px-2 py-0.5 ${TONES[chip.tone]}`}>{chip.text}</span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-theme-text-muted">
        <span>
          Checked {Number.isFinite(checkedAt) ? new Date(checkedAt).toLocaleDateString() : 'earlier'}
          {facts.buildInfo ? ` on llama.cpp ${String(facts.buildInfo).split('-')[0]}` : ''}
          {profile.result?.status === 'partial' ? '; some checks ran out of time' : ''}.
        </span>
        {recheckButton}
        {hasWarning && !showOffer && <HelpLink className="text-[11px]" />}
      </div>
      {showOffer && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-theme-text-secondary">
          <span>
            {offer.supported
              ? `A fixed chat template is available for this model. ${offer.reason}`
              : 'A fixed chat template is available for this model, but the Windows model runtime on this machine cannot use it yet.'}
          </span>
          {offer.supported && (
            <button
              type="button"
              onClick={tryFixedTemplate}
              disabled={applying || rechecking}
              className="inline-flex items-center gap-1 rounded border border-theme-border px-2 py-0.5 text-theme-text-secondary hover:text-theme-text disabled:opacity-50"
            >
              {applying ? <Loader2 size={11} className="animate-spin" /> : <Wrench size={11} />}
              {applying ? 'Restarting the model…' : 'Try a fixed template'}
            </button>
          )}
          <HelpLink className="text-[11px]" />
        </div>
      )}
      {notice && <p role="status" className="text-amber-200">{notice}</p>}
    </section>
  )
}
