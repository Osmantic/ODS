import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Link } from 'react-router-dom'
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Code2,
  Copy,
  Globe2,
  ExternalLink,
  Loader2,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Wrench,
  X,
} from 'lucide-react'

const MARKDOWN_COMPONENTS = {
  p: ({ children }) => <p className="break-words [&:not(:first-child)]:mt-3">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="break-words">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ inline, children }) => inline
    ? <code className="rounded border border-theme-border bg-theme-bg/70 px-1 py-0.5 font-mono text-[13px] text-theme-text">{children}</code>
    : <code className="block whitespace-pre-wrap break-words rounded bg-theme-bg/70 p-2 font-mono text-[13px] text-theme-text">{children}</code>,
  pre: ({ children }) => <pre className="my-2 overflow-x-auto rounded border border-theme-border bg-theme-bg/70">{children}</pre>,
  a: ({ href, children }) => {
    const safe = typeof href === 'string' && /^https?:\/\//i.test(href)
    return safe
      ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-theme-accent-light underline">{children}</a>
      : <span>{children}</span>
  },
}

const MAX_INPUT_LEN = 16 * 1024
const MAX_REQUEST_MESSAGES = 50
const MAX_TOTAL_MESSAGE_BYTES = 256 * 1024
const CHAT_STORAGE_KEY = 'ods.pixel.chat.v1'
const SAFE_CHAT_ID = /^[A-Za-z0-9_-]{1,128}$/
const STOPPED_NOTICE = 'Stopped by you. Workspace changes completed before cancellation were preserved.'
const MODEL_SWITCH_DETAIL = 'Model switch in progress; Pixel will be ready when activation completes'
const CLEAN_CONTEXT_RECOVERY_REASON = 'operations-unavailable-zero-submissions'
const CLEAN_CONTEXT_RECOVERY_NOTICE = 'The first attempt did not reach the Operations Broker, and the host verified that no work was submitted. Retrying once with a clean context…'
const CLEAN_CONTEXT_RECOVERY_FAILED = 'Automatic recovery was attempted once, but Pixel again did not reach the Operations Broker. The host verified that no Operations work was submitted. Nothing was executed. Start a new chat or rephrase the request.'
const STATUS_POLL_MS = 3000
const OPS_STATUS_POLL_MS = 3000
const OPS_TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'rejected'])
const OPS_APPROVAL_RECEIPT = /^Pixel prepared the exact (ods\.extensions\.(?:install|enable|disable|remove)) plan for extension ([a-z0-9](?:[a-z0-9_-]|\.(?=[a-z0-9])){0,63}), but external approval is required\. No lifecycle change was executed\. Job: (ops-[0-9]{13}-[a-f0-9]{12})\. Plan SHA-256: ([a-f0-9]{64})\.$/
const OPS_HOST_COMMAND_APPROVAL_RECEIPT = /^Pixel prepared a protected ODS host command plan, but external approval is required\. No command was executed\. Job: (ops-[0-9]{13}-[a-f0-9]{12})\. Plan SHA-256: ([a-f0-9]{64})\.$/
let fallbackChatSequence = 0

const SUGGESTED_TASKS = [
  {
    icon: ShieldCheck,
    label: 'Check ODS health',
    description: 'Inspect the live stack and explain anything that needs attention.',
    prompt: 'Check the current ODS status. Summarize what is healthy, identify anything degraded or stopped, and suggest the safest next action.',
  },
  {
    icon: Code2,
    label: 'Build in my workspace',
    description: 'Create, edit, run, and verify code instead of only suggesting it.',
    prompt: 'Help me build and verify something useful in your writable workspace. Start by asking what outcome I want if it is not clear.',
  },
  {
    icon: Globe2,
    label: 'Research with sources',
    description: 'Use public web evidence and keep citations exact.',
    prompt: 'Research a public topic for me using current sources. Ask what topic and decision I need to make, then return a concise evidence-backed answer with exact source URLs.',
  },
  {
    icon: Wrench,
    label: 'Plan a multi-step task',
    description: 'Break down a larger job, use tools, and report verified progress.',
    prompt: 'Help me complete a multi-step task safely. Ask for the objective, then make a short plan and carry out the steps you can verify.',
  },
]

function formatContext(value) {
  const context = Number(value || 0)
  if (!Number.isFinite(context) || context <= 0) return ''
  if (context >= 1024 && context % 1024 === 0) return `${context / 1024}K context`
  return `${context.toLocaleString()} context`
}

export function formatElapsed(value) {
  const totalSeconds = Math.max(0, Math.floor(Number(value) || 0))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

export function parseApprovalReceipt(content) {
  if (typeof content !== 'string') return null
  const match = content.trim().match(OPS_APPROVAL_RECEIPT)
  if (match) {
    return {
      action: match[1],
      extensionId: match[2],
      jobId: match[3],
      planHash: match[4],
    }
  }
  const hostCommand = content.trim().match(OPS_HOST_COMMAND_APPROVAL_RECEIPT)
  return hostCommand ? {
    action: 'raw-shell',
    extensionId: 'ods-host',
    jobId: hostCommand[1],
    planHash: hostCommand[2],
  } : null
}

export function isCleanContextRecoveryFrame(frame) {
  const marker = frame?.pixel
  return Boolean(
    frame?.choices?.[0]?.finish_reason === 'stop'
    && marker
    && typeof marker === 'object'
    && !Array.isArray(marker)
    && Object.keys(marker).sort().join('\n') === ['reason', 'recovery', 'schemaVersion'].join('\n')
    && marker.schemaVersion === 1
    && marker.recovery === 'clean-context'
    && marker.reason === CLEAN_CONTEXT_RECOVERY_REASON
  )
}

export function parseVerifiedPreviewFrame(frame) {
  const marker = frame?.pixel
  const preview = marker?.preview
  const markerKeys = marker && typeof marker === 'object' && !Array.isArray(marker)
    ? Object.keys(marker).sort().join('\n')
    : ''
  const previewKeys = preview && typeof preview === 'object' && !Array.isArray(preview)
    ? Object.keys(preview).sort().join('\n')
    : ''
  if (
    frame?.choices?.[0]?.finish_reason !== 'stop'
    || markerKeys !== ['preview', 'schemaVersion'].join('\n')
    || marker.schemaVersion !== 1
    || previewKeys !== [
      'bytes',
      'entrySha256',
      'files',
      'kind',
      'port',
      'relativeDirectory',
      'schemaVersion',
      'sha256',
      'siteId',
      'url',
    ].join('\n')
    || preview.schemaVersion !== 1
    || preview.kind !== 'ods-pixel-workspace-preview'
    || typeof preview.relativeDirectory !== 'string'
    || !/^(?!\/)(?!.*(?:^|\/)\.\.?(?:\/|$))[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$/.test(preview.relativeDirectory)
    || !/^site-[a-f0-9]{24}$/.test(preview.siteId)
    || preview.siteId !== `site-${preview.sha256?.slice(0, 24)}`
    || !Number.isInteger(preview.port)
    || preview.port < 1
    || preview.port > 65535
    || preview.url !==
      `http://${preview.siteId}.localhost:${preview.port}/${preview.siteId}/`
    || !Number.isInteger(preview.files)
    || preview.files < 1
    || preview.files > 128
    || !Number.isInteger(preview.bytes)
    || preview.bytes < 1
    || preview.bytes > 16 * 1024 * 1024
    || !/^[a-f0-9]{64}$/.test(preview.sha256)
    || !/^[a-f0-9]{64}$/.test(preview.entrySha256)
  ) return null
  return { ...preview }
}

export function resolvePreviewAccess(preview, browserLocation = globalThis.location) {
  if (!preview) return null
  const hostname = typeof browserLocation?.hostname === 'string'
    ? browserLocation.hostname.toLowerCase()
    : ''
  const loopback = hostname === 'localhost'
    || hostname === '127.0.0.1'
    || hostname === '[::1]'
    || hostname === '::1'
  if (loopback || !/^https?:$/.test(browserLocation?.protocol || '')) {
    return {
      url: preview.url,
      sandbox: 'allow-scripts allow-same-origin',
      route: 'loopback',
    }
  }
  return {
    url: `/pixel-preview/${preview.siteId}/`,
    sandbox: 'allow-scripts',
    route: 'private-dashboard',
  }
}

export function OperationsApprovalCard({ content }) {
  const receipt = parseApprovalReceipt(content)
  const [projection, setProjection] = useState(null)
  const [verification, setVerification] = useState(receipt ? 'loading' : 'absent')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!receipt) return undefined
    setVerification('loading')
    setProjection(null)
    const controller = new AbortController()
    let stopped = false
    let poll = null

    async function fetchProjection() {
      try {
        const response = await fetch(
          `/api/pixel/ops/${receipt.jobId}?plan_hash=${receipt.planHash}`,
          { signal: controller.signal }
        )
        if (!response.ok) throw new Error('status unavailable')
        const value = await response.json()
        if (
          value?.schemaVersion !== 1
          || value?.kind !== 'ods-pixel-operations-status'
          || value?.jobId !== receipt.jobId
          || value?.planHash !== receipt.planHash
          || typeof value?.status !== 'string'
          || typeof value?.approvalRequired !== 'boolean'
          || typeof value?.riskTier !== 'string'
          || (value?.approvalCommand !== null && typeof value?.approvalCommand !== 'string')
        ) throw new Error('invalid status')
        setProjection(value)
        setVerification('verified')
        if (!stopped && !OPS_TERMINAL_STATUSES.has(value.status)) {
          poll = globalThis.setTimeout(fetchProjection, OPS_STATUS_POLL_MS)
        }
      } catch (error) {
        if (error?.name !== 'AbortError') {
          setProjection(null)
          setVerification('unverified')
          if (!stopped) {
            poll = globalThis.setTimeout(fetchProjection, OPS_STATUS_POLL_MS)
          }
        }
      }
    }

    fetchProjection()
    return () => {
      stopped = true
      if (poll !== null) globalThis.clearTimeout(poll)
      controller.abort()
    }
  }, [receipt?.jobId, receipt?.planHash])

  if (!receipt) return null

  const copyCommand = async () => {
    if (!projection?.approvalCommand) return
    try {
      await globalThis.navigator?.clipboard?.writeText(projection.approvalCommand)
      setCopied(true)
      globalThis.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }

  if (verification === 'loading' || verification === 'absent') {
    return (
      <div role="status" className="mt-3 flex items-center gap-2 rounded-xl border border-theme-border bg-theme-bg/55 px-3 py-2 text-xs text-theme-text-muted">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-theme-accent-light" />
        Verifying the immutable broker receipt…
      </div>
    )
  }
  if (verification !== 'verified') {
    return (
      <div role="alert" className="mt-3 rounded-xl border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-200">
        This approval receipt could not be independently verified. Do not approve it.
      </div>
    )
  }

  const awaiting = projection.status === 'awaiting-approval' && projection.approvalRequired
  const succeeded = projection.status === 'succeeded'
  return (
    <div className={`mt-3 rounded-xl border p-3 ${
      succeeded
        ? 'border-emerald-500/30 bg-emerald-500/10'
        : awaiting
          ? 'border-amber-500/30 bg-amber-500/10'
          : 'border-theme-border bg-theme-bg/55'
    }`}>
      <div className="flex items-start gap-2.5">
        {succeeded
          ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
          : <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />}
        <div className="min-w-0 flex-1">
          <p className="font-medium text-theme-text">
            {succeeded ? 'Protected operation completed' : awaiting ? 'Owner approval required' : `Broker status: ${projection.status}`}
          </p>
          <p className="mt-1 text-xs leading-5 text-theme-text-muted">
            The host independently matched this job and plan hash. Approval cannot happen through Pixel or model text.
          </p>
          <dl className="mt-2 grid gap-x-3 gap-y-1 font-mono text-[10px] text-theme-text-muted sm:grid-cols-[auto_1fr]">
            <dt>Requested</dt><dd className="truncate text-theme-text-secondary">{receipt.action} · {receipt.extensionId}</dd>
            <dt>Risk</dt><dd className="text-theme-text-secondary">{projection.riskTier}</dd>
            <dt>Job</dt><dd className="truncate text-theme-text-secondary">{receipt.jobId}</dd>
            <dt>Plan</dt><dd className="truncate text-theme-text-secondary" title={receipt.planHash}>{receipt.planHash}</dd>
          </dl>
          {awaiting && projection.approvalCommand && (
            <>
              <button
                type="button"
                onClick={copyCommand}
                className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-1.5 text-xs font-medium text-amber-200 transition hover:bg-amber-400/15"
              >
                {copied ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? 'Copied' : 'Copy secure approval command'}
              </button>
              <p className="mt-2 flex items-start gap-1.5 text-[10px] leading-4 text-theme-text-muted">
                <Terminal className="mt-0.5 h-3 w-3 shrink-0" />
                Run it in a real terminal. Pixel will require fresh password-backed administrator authentication, show the complete protected plan, and ask for a one-time challenge.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function workingDetail(elapsedSeconds) {
  if (elapsedSeconds < 15) return 'Starting the owner-agent turn'
  if (elapsedSeconds < 60) return 'Pixel is working with the active model'
  return 'Still working — local model and tool turns can take several minutes'
}

function makeChatId() {
  const cryptoApi = globalThis.crypto
  if (cryptoApi?.randomUUID) return cryptoApi.randomUUID()
  if (cryptoApi?.getRandomValues) {
    const bytes = new Uint8Array(16)
    cryptoApi.getRandomValues(bytes)
    return `chat-${Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')}`
  }
  fallbackChatSequence += 1
  return `chat-${Date.now()}-${fallbackChatSequence}`
}

function replaceLastAssistant(messages, update) {
  const index = messages.length - 1
  if (index < 0 || messages[index]?.role !== 'assistant') return messages
  const next = [...messages]
  next[index] = { ...next[index], ...update }
  return next
}

function stoppedContent(content) {
  const partial = typeof content === 'string' ? content.trimEnd() : ''
  if (!partial) return STOPPED_NOTICE
  if (partial.includes(STOPPED_NOTICE)) return partial
  return `${partial}\n\n---\n\n_${STOPPED_NOTICE}_`
}

function loadStoredChat() {
  try {
    const stored = JSON.parse(globalThis.localStorage?.getItem(CHAT_STORAGE_KEY) || 'null')
    if (
      stored?.schema !== 1
      || !SAFE_CHAT_ID.test(stored.chatId || '')
      || !Array.isArray(stored.messages)
      || stored.messages.length > MAX_REQUEST_MESSAGES
    ) return null

    let totalBytes = 0
    const messages = stored.messages.map((message) => {
      if (
        !message
        || !['user', 'assistant'].includes(message.role)
        || typeof message.content !== 'string'
        || message.content.length > MAX_INPUT_LEN
      ) throw new Error('invalid stored Pixel message')
      totalBytes += new TextEncoder().encode(message.content).byteLength
      if (totalBytes > MAX_TOTAL_MESSAGE_BYTES) throw new Error('stored Pixel chat is too large')
      return { role: message.role, content: message.content }
    })
    return { chatId: stored.chatId, messages }
  } catch {
    return null
  }
}

function boundedHistory(messages, nextUserContent) {
  const encoder = new TextEncoder()
  const budget = MAX_TOTAL_MESSAGE_BYTES - encoder.encode(nextUserContent).byteLength
  const selected = []
  let bytes = 0
  for (let index = messages.length - 1; index >= 0 && selected.length < MAX_REQUEST_MESSAGES - 2; index -= 1) {
    const { role, content } = messages[index]
    const size = encoder.encode(content).byteLength
    if (bytes + size > budget) break
    selected.unshift({ role, content })
    bytes += size
  }
  while (selected[0]?.role === 'assistant') selected.shift()
  return selected
}

export default function Pixel({ systemStatus = null }) {
  const [initialChat] = useState(loadStoredChat)
  const [status, setStatus] = useState('loading')
  const [statusDetail, setStatusDetail] = useState('')
  const [messages, setMessages] = useState(() => initialChat?.messages || [])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [stopError, setStopError] = useState('')
  const [workingElapsedSeconds, setWorkingElapsedSeconds] = useState(0)
  const [agentRuntime, setAgentRuntime] = useState(null)
  const [modelSupport, setModelSupport] = useState(null)
  const [preview, setPreview] = useState(null)
  const [previewRefresh, setPreviewRefresh] = useState(0)

  const abortRef = useRef(null)
  const chatIdRef = useRef(initialChat?.chatId || makeChatId())
  const contextStartRef = useRef(0)
  const inputRef = useRef(null)
  const scrollRef = useRef(null)

  const activeModel = agentRuntime?.model || systemStatus?.inference?.loadedModel || systemStatus?.model?.name || ''
  const activeContext = formatContext(
    agentRuntime?.contextLength || systemStatus?.inference?.contextSize || systemStatus?.model?.contextLength
  )
  const previewAccess = resolvePreviewAccess(preview)

  useEffect(() => {
    const controller = new AbortController()
    let stopped = false
    let poll = null
    async function fetchStatus() {
      try {
        const response = await fetch('/api/pixel/status', { signal: controller.signal })
        if (!response.ok) throw new Error('status unavailable')
        const data = await response.json()
        const runtime = data?.runtime
        const runtimeKeys = runtime && typeof runtime === 'object' && !Array.isArray(runtime)
          ? Object.keys(runtime).sort().join('\n')
          : ''
        const validRemoteRuntime = runtimeKeys === ['contextLength', 'maxTokens', 'model', 'reasoning', 'source'].join('\n')
          && runtime.source === 'remote-provider'
          && Number.isInteger(runtime.maxTokens)
          && runtime.maxTokens >= 1
          && runtime.maxTokens <= runtime.contextLength
          && typeof runtime.reasoning === 'boolean'
          && runtime.contextLength >= 4096
        const validLocalRuntime = runtimeKeys === ['contextLength', 'model', 'source'].join('\n')
          && runtime.source === 'local-switchboard'
        setAgentRuntime(
          (validRemoteRuntime || validLocalRuntime)
          && typeof runtime.model === 'string'
          && runtime.model.length > 0
          && runtime.model.length <= 256
          && Number.isInteger(runtime.contextLength)
          && runtime.contextLength >= 1
          && runtime.contextLength <= 10_000_000
            ? runtime
            : null
        )
        const support = data?.modelSupport
        const supportKeys = support && typeof support === 'object' && !Array.isArray(support)
          ? Object.keys(support).sort().join('\n')
          : ''
        const validatedSupport = supportKeys === ['detail', 'tier'].join('\n')
          && support.tier === 'adaptive'
          && typeof support.detail === 'string'
          && support.detail.length > 0
          && support.detail.length <= 512
          ? support
          : null
        // Treat the former hard-gate status as an advisory during rolling
        // upgrades so a stale API cannot make the new UI exclude a model.
        const legacyAdaptive = data.state === 'model_incompatible'
        setModelSupport(validatedSupport || (legacyAdaptive
          ? {
              tier: 'adaptive',
              detail: typeof data.detail === 'string' && data.detail.trim()
                ? data.detail
                : 'Pixel is ready and will adapt its tool flow for this model.',
            }
          : null))
        setStatus(data.available === true || legacyAdaptive
          ? 'available'
          : data.state === 'model_switching'
            ? 'switching'
            : 'unavailable')
        setStatusDetail(typeof data.detail === 'string' ? data.detail : '')
      } catch (error) {
        if (error?.name !== 'AbortError') {
          setStatus('unavailable')
          setStatusDetail('Could not reach Pixel backend')
        }
      } finally {
        if (!stopped) poll = globalThis.setTimeout(fetchStatus, STATUS_POLL_MS)
      }
    }
    fetchStatus()
    return () => {
      stopped = true
      if (poll !== null) globalThis.clearTimeout(poll)
      controller.abort()
    }
  }, [])

  useEffect(() => () => abortRef.current?.abort(), [])

  useEffect(() => {
    if (!sending) {
      setWorkingElapsedSeconds(0)
      return undefined
    }
    const startedAt = Date.now()
    const updateElapsed = () => {
      setWorkingElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)))
    }
    updateElapsed()
    const timer = globalThis.setInterval(updateElapsed, 1000)
    return () => globalThis.clearInterval(timer)
  }, [sending])

  useEffect(() => {
    scrollRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    const field = inputRef.current
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, 160)}px`
  }, [input])

  useEffect(() => {
    if (sending) return
    try {
      const storedMessages = boundedHistory(messages.slice(contextStartRef.current), '')
      globalThis.localStorage?.setItem(CHAT_STORAGE_KEY, JSON.stringify({
        schema: 1,
        chatId: chatIdRef.current,
        messages: storedMessages.map(({ role, content }) => ({
          role,
          content,
        })),
      }))
    } catch {
      // Conversation persistence is a convenience; chat remains usable when
      // storage is unavailable, full, or blocked by the browser.
    }
  }, [messages, sending])

  const sendMessage = useCallback(async () => {
    const trimmed = input.trim()
    if (!trimmed || sending || status !== 'available' || trimmed.length > MAX_INPUT_LEN) return

    const userMessage = { role: 'user', content: trimmed }
    const originalContextStart = contextStartRef.current
    // Local assistant messages carry UI-only status metadata. Keep the API
    // boundary exact so a completed or failed first turn cannot make the next
    // request fail the dashboard API's extra="forbid" contract.
    const conversation = [
      ...boundedHistory(messages.slice(originalContextStart), trimmed),
      userMessage,
    ]
    contextStartRef.current = 0
    setMessages([...conversation, { role: 'assistant', content: '', status: 'streaming' }])
    setInput('')
    setSending(true)
    setStopping(false)
    setStopError('')

    const controller = new AbortController()
    abortRef.current = controller
    let latestAssistantText = ''

    async function streamAttempt(chatId, attemptConversation) {
      let reader
      let assistantText = ''
      let receivedDone = false
      let receivedError = false
      let recoveryEligible = false
      let verifiedPreview = null

      try {
        const response = await fetch('/api/pixel/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chat_id: chatId, messages: attemptConversation }),
          signal: controller.signal,
        })
        if (response.status === 409) {
          let detail = MODEL_SWITCH_DETAIL
          if (typeof response.json === 'function') {
            try {
              const payload = await response.json()
              if (typeof payload?.detail === 'string' && payload.detail.trim()) detail = payload.detail
            } catch {
              // The fixed local fallback remains safe and actionable.
            }
          }
          return { kind: 'switching', detail }
        }
        if (response.status === 412) {
          let detail = 'Pixel can use this model, but the current runtime still has an older model gate.'
          if (typeof response.json === 'function') {
            try {
              const payload = await response.json()
              if (typeof payload?.detail === 'string' && payload.detail.trim()) detail = payload.detail
            } catch {
              // The fixed local fallback remains safe and actionable.
            }
          }
          return { kind: 'adaptive', detail }
        }
        if (!response.ok) throw new Error('chat unavailable')

        reader = response.body?.getReader()
        if (!reader) throw new Error('stream unavailable')

        const decoder = new TextDecoder()
        let buffer = ''

        while (!receivedDone) {
          const { done, value } = await reader.read()
          if (done) {
            buffer += decoder.decode()
            break
          }
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const rawLine of lines) {
            const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
            if (!line.startsWith('data:')) continue
            const payload = line.slice(5).trimStart()
            if (payload === '[DONE]') {
              receivedDone = true
              break
            }

            try {
              const frame = JSON.parse(payload)
              if (frame?.error) {
                receivedError = true
                setMessages(previous => replaceLastAssistant(previous, {
                  content: 'Pixel could not complete the response.',
                  status: 'error',
                }))
                continue
              }
              if (isCleanContextRecoveryFrame(frame)) recoveryEligible = true
              const candidatePreview = parseVerifiedPreviewFrame(frame)
              if (candidatePreview) verifiedPreview = candidatePreview
              const content = frame?.choices?.[0]?.delta?.content
              if (typeof content === 'string' && content.length > 0) {
                assistantText += content
                latestAssistantText = assistantText
                setMessages(previous => replaceLastAssistant(previous, {
                  content: assistantText,
                  status: 'streaming',
                }))
              }
            } catch {
              // Ignore malformed data frames; the server bounds and terminates the stream.
            }
          }
        }

        return {
          kind: 'complete',
          assistantText,
          receivedDone,
          receivedError,
          recoveryEligible,
          verifiedPreview,
        }
      } finally {
        reader?.releaseLock?.()
      }
    }

    function finishAttempt(attempt, recovered = false) {
      if (attempt.receivedError) return
      if (attempt.receivedDone) {
        if (attempt.verifiedPreview) {
          setPreview(attempt.verifiedPreview)
          setPreviewRefresh(0)
        }
        setMessages(previous => replaceLastAssistant(previous, {
          status: 'done',
          ...(recovered ? { recovered: true } : {}),
        }))
        return
      }
      const content = attempt.assistantText
        ? `${attempt.assistantText}\n\n_Response interrupted._`
        : 'Connection interrupted'
      setMessages(previous => replaceLastAssistant(previous, { content, status: 'error' }))
    }

    try {
      let attempt = await streamAttempt(chatIdRef.current, conversation)
      if (attempt.kind === 'switching') {
        setStatus('switching')
        setStatusDetail(attempt.detail)
        setInput(trimmed)
        contextStartRef.current = originalContextStart
        setMessages(messages)
        return
      }
      if (attempt.kind === 'adaptive') {
        setStatus('available')
        setModelSupport({ tier: 'adaptive', detail: attempt.detail })
        setInput(trimmed)
        contextStartRef.current = originalContextStart
        setMessages(messages)
        return
      }

      if (!attempt.receivedError && attempt.receivedDone && attempt.recoveryEligible) {
        const retryChatId = makeChatId()
        chatIdRef.current = retryChatId
        contextStartRef.current = conversation.length - 1
        latestAssistantText = ''
        setMessages([
          ...conversation,
          {
            role: 'assistant',
            content: CLEAN_CONTEXT_RECOVERY_NOTICE,
            status: 'recovering',
          },
        ])

        attempt = await streamAttempt(retryChatId, [userMessage])
        if (attempt.kind === 'switching') {
          contextStartRef.current = 0
          setMessages([])
          setInput(trimmed)
          setStatus('switching')
          setStatusDetail(`${attempt.detail}. The clean-context request is preserved.`)
          return
        }
        if (attempt.kind === 'adaptive') {
          contextStartRef.current = 0
          setMessages([])
          setInput(trimmed)
          setStatus('available')
          setModelSupport({ tier: 'adaptive', detail: attempt.detail })
          return
        }
        if (!attempt.receivedError && attempt.receivedDone && attempt.recoveryEligible) {
          setMessages(previous => replaceLastAssistant(previous, {
            content: CLEAN_CONTEXT_RECOVERY_FAILED,
            status: 'error',
          }))
          return
        }
        finishAttempt(attempt, true)
        return
      }

      finishAttempt(attempt)
    } catch (error) {
      if (error?.name !== 'AbortError') {
        setMessages(previous => replaceLastAssistant(previous, {
          content: latestAssistantText || 'Request failed',
          status: 'error',
        }))
      }
    } finally {
      setSending(false)
      setStopping(false)
      setStopError('')
      if (abortRef.current === controller) abortRef.current = null
    }
  }, [input, messages, sending, status])

  const stopStreaming = useCallback(async () => {
    const controller = abortRef.current
    if (!controller || stopping) return

    setStopping(true)
    setStopError('')
    try {
      const response = await fetch('/api/pixel/chat/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatIdRef.current }),
      })
      let payload = null
      if (typeof response?.json === 'function') {
        try {
          payload = await response.json()
        } catch {
          // The exact acknowledgement check below fails closed.
        }
      }
      if (!response?.ok || !payload || Object.keys(payload).length !== 1 || payload.aborted !== true) {
        throw new Error('cancellation was not acknowledged')
      }

      // A normal terminal response may win the cancellation race. Do not
      // rewrite that completed answer as owner-stopped.
      if (abortRef.current !== controller) return
      controller.abort()
      abortRef.current = null
      setMessages(previous => replaceLastAssistant(previous, {
        content: stoppedContent(previous.at(-1)?.content),
        status: 'stopped',
      }))
      setSending(false)
    } catch {
      // Keep the live stream attached and Stop retryable. Claiming success
      // without an exact acknowledgement could leave tools or inference active.
      if (abortRef.current === controller) {
        setStopError('Stop was not confirmed. Pixel is still connected; retry Stop.')
      }
    } finally {
      setStopping(false)
    }
  }, [stopping])

  const startNewChat = useCallback(() => {
    if (sending) return
    chatIdRef.current = makeChatId()
    contextStartRef.current = 0
    setMessages([])
    setPreview(null)
    setPreviewRefresh(0)
    setInput('')
    inputRef.current?.focus?.()
  }, [sending])

  const selectSuggestion = useCallback((prompt) => {
    setInput(prompt)
    inputRef.current?.focus?.()
  }, [])

  const inputOver = input.length > MAX_INPUT_LEN
  const inputEmpty = !input.trim()
  const isDisabled = sending || status !== 'available'
  const workingElapsed = formatElapsed(workingElapsedSeconds)
  const statusLabel = stopping
    ? 'Stopping'
    : sending
      ? 'Working'
    : status === 'available'
      ? 'Available'
      : status === 'switching'
        ? 'Switching model...'
      : status === 'loading'
        ? 'Connecting...'
        : 'Degraded'

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden text-theme-text">
      <div className="flex flex-wrap items-center gap-3 border-b border-theme-border bg-theme-card/35 px-4 py-3 sm:px-6">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-theme-accent/30 bg-theme-accent/15 text-theme-accent-light">
          <Bot className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h1 className="text-base font-semibold leading-tight">Pixel</h1>
          <p className="text-[11px] text-theme-text-muted">Your local ODS owner agent</p>
        </div>

        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
          {activeModel && (
            <div
              className="hidden min-w-0 items-center gap-2 rounded-lg border border-theme-border bg-theme-bg/40 px-2.5 py-1.5 font-mono text-[10px] text-theme-text-muted sm:flex"
              title={activeModel}
            >
              <span className="max-w-52 truncate text-theme-text-secondary">{activeModel}</span>
              {activeContext && <span className="shrink-0 text-theme-accent-light">{activeContext}</span>}
            </div>
          )}
          <Link
            to="/models"
            className="hidden rounded-lg px-2.5 py-1.5 text-xs font-medium text-theme-text-muted transition hover:bg-theme-surface-hover hover:text-theme-text sm:inline-flex"
          >
            Change model
          </Link>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={startNewChat}
              disabled={sending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-theme-border bg-theme-card px-2.5 py-1.5 text-xs font-medium text-theme-text-secondary transition hover:border-theme-accent/40 hover:text-theme-text disabled:cursor-not-allowed disabled:opacity-50"
              title="Start a new chat"
            >
              <Plus className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">New chat</span>
            </button>
          )}
          <span
            aria-live="polite"
            title={modelSupport?.detail || undefined}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium ${
            sending
              ? 'border-theme-accent/35 bg-theme-accent/15 text-theme-accent-light'
              : status === 'available'
              ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-400'
              : 'border-amber-500/25 bg-amber-500/10 text-amber-300'
          }`}
          >
            {sending || status === 'loading' || status === 'switching' ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <span className={`h-1.5 w-1.5 rounded-full ${
                status === 'available' ? 'bg-emerald-400' : 'bg-amber-300'
              }`} />
            )}
            {statusLabel}
            {sending && <span className="font-mono text-[10px] opacity-80">{workingElapsed}</span>}
          </span>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-6">
        {status === 'loading' && messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center text-theme-text-muted">
            <Loader2 className="mb-3 h-8 w-8 animate-spin" />
            <p>Connecting to Pixel...</p>
          </div>
        )}
        {status === 'unavailable' && messages.length === 0 && (
          <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center text-center text-theme-text-muted">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-amber-500/25 bg-amber-500/10 text-amber-300">
              <AlertCircle className="h-7 w-7" />
            </div>
            <p className="font-medium text-theme-text">Pixel is currently unavailable</p>
            {statusDetail && <p className="mt-1 text-sm">{statusDetail}</p>}
            <p className="mt-4 text-xs">Your other ODS applications remain available while the agent reconnects.</p>
          </div>
        )}
        {status === 'switching' && messages.length === 0 && (
          <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center text-center text-theme-text-muted">
            <Loader2 className="mb-4 h-9 w-9 animate-spin text-theme-accent-light" />
            <p className="font-medium text-theme-text">Pixel is switching models</p>
            <p className="mt-1 text-sm">Your draft is safe. Pixel will reconnect automatically when activation completes.</p>
          </div>
        )}
        {status === 'available' && messages.length === 0 && (
          <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col justify-center py-8">
            <div className="mb-7 text-center">
              <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-theme-accent/30 bg-theme-accent/15 text-theme-accent-light shadow-[0_0_32px_rgba(157,0,255,0.18)]">
                <Sparkles className="h-7 w-7" />
              </div>
              <h2 className="text-2xl font-semibold tracking-tight">What should we accomplish?</h2>
              <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-theme-text-muted">
                Pixel uses every ODS chat model. Stronger models handle complex tools and long tasks more reliably; the same broker-enforced safety boundaries apply to all of them.
              </p>
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              {SUGGESTED_TASKS.map(({ icon: Icon, label, description, prompt }) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => selectSuggestion(prompt)}
                  className="group flex items-start gap-3 rounded-xl border border-theme-border bg-theme-card/70 p-4 text-left transition hover:border-theme-accent/45 hover:bg-theme-surface-hover"
                >
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-theme-accent/12 text-theme-accent-light">
                    <Icon className="h-4 w-4" />
                  </span>
                  <span>
                    <span className="block text-sm font-medium text-theme-text">{label}</span>
                    <span className="mt-1 block text-xs leading-5 text-theme-text-muted">{description}</span>
                  </span>
                </button>
              ))}
            </div>

            <div className="mt-6 flex flex-wrap justify-center gap-x-4 gap-y-2 text-[11px] text-theme-text-muted">
              <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5" /> Local-first</span>
              <span>Workspace tools</span>
              <span>Public-source research</span>
              <span>Explicit safety boundaries</span>
            </div>
          </div>
        )}
        {messages.map((message, index) => (
          <div key={index} className={`mx-auto flex w-full max-w-5xl ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-6 xl:max-w-3xl ${
              message.role === 'user'
                ? 'bg-theme-accent text-white shadow-lg shadow-black/10'
                : message.status === 'error'
                  ? 'border border-red-500/25 bg-red-500/10 text-red-200'
                  : message.status === 'stopped'
                    ? 'border border-amber-500/30 bg-amber-500/10 text-theme-text-secondary shadow-lg shadow-black/10'
                  : 'border border-theme-border bg-theme-card text-theme-text-secondary shadow-lg shadow-black/10'
            }`}>
              {message.status === 'stopped' && (
                <div role="status" className="mb-2 inline-flex items-center gap-1.5 text-xs font-medium text-amber-300">
                  <Square className="h-3 w-3 fill-current" />
                  Response stopped
                </div>
              )}
              {message.recovered && (
                <div role="status" className="mb-2 inline-flex items-center gap-1.5 text-xs font-medium text-emerald-300">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Recovered with a clean context
                </div>
              )}
              {message.role === 'assistant' && message.content ? (
                <>
                  <ReactMarkdown components={MARKDOWN_COMPONENTS}>{message.content}</ReactMarkdown>
                  <OperationsApprovalCard content={message.content} />
                </>
              ) : (
                <span className="break-words whitespace-pre-wrap">{message.content}</span>
              )}
              {message.status === 'streaming' && !message.content && (
                <span role="status" className="inline-flex items-start gap-2 text-theme-text-muted">
                  <Loader2 className="h-4 w-4 animate-spin text-theme-accent-light" />
                  <span>
                    <span className="block">{workingDetail(workingElapsedSeconds)}</span>
                    <span className="mt-0.5 block text-xs text-theme-text-muted/80">
                      {workingElapsed} elapsed · You can stop safely at any time.
                    </span>
                  </span>
                </span>
              )}
            </div>
          </div>
        ))}
        <div ref={scrollRef} />
      </div>

      <div className="border-t border-theme-border bg-theme-bg/80 px-4 py-3 backdrop-blur sm:px-6">
        <div className="mx-auto max-w-5xl">
          <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                if (event.nativeEvent?.isComposing) return
                event.preventDefault()
                sendMessage()
              }
            }}
            placeholder={status === 'available'
              ? 'Message Pixel...'
              : status === 'switching'
                ? 'Waiting for model switch...'
                : 'Pixel is unavailable'}
            disabled={isDisabled}
            rows={1}
            className={`min-h-11 flex-1 resize-none rounded-xl border bg-theme-card px-4 py-2.5 text-sm text-theme-text outline-none transition placeholder:text-theme-text-muted/70 focus:ring-2 focus:ring-theme-accent/30 disabled:opacity-50 ${
              inputOver ? 'border-red-400' : 'border-theme-border focus:border-theme-accent/60'
            }`}
          />
          {sending ? (
            <button
              onClick={stopStreaming}
              disabled={stopping}
              className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-red-500 text-white transition hover:bg-red-600 disabled:cursor-wait disabled:opacity-70"
              title={stopping ? 'Stopping' : 'Stop'}
            >
              {stopping ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={isDisabled || inputOver || inputEmpty}
              className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-theme-accent text-white shadow-[0_0_22px_rgba(157,0,255,0.18)] transition hover:bg-theme-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
              title="Send"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
          </div>
          {stopError && <p role="alert" className="mt-1.5 px-1 text-xs text-amber-300">{stopError}</p>}
          <div className="mt-1.5 flex items-center justify-between gap-3 px-1 text-[10px] text-theme-text-muted/70">
            <span>{stopping ? 'Waiting for exact cancellation acknowledgement' : sending ? `Pixel is using the active ODS model and tools · ${workingElapsed} elapsed` : 'Enter to send • Shift+Enter for a new line'}</span>
            <span className={inputOver ? 'text-red-400' : ''}>{input.length.toLocaleString()} / {MAX_INPUT_LEN.toLocaleString()}</span>
          </div>
        </div>
        {inputOver && (
          <p className="mx-auto mt-1 max-w-5xl px-1 text-xs text-red-400">
            Message too long (max {MAX_INPUT_LEN.toLocaleString()} characters)
          </p>
        )}
      </div>
        </div>

        {preview && (
          <aside className="flex h-[46vh] min-h-80 shrink-0 flex-col border-t border-theme-border bg-theme-card/55 lg:h-auto lg:w-[42%] lg:border-l lg:border-t-0 xl:w-1/2">
            <div className="flex items-center gap-2 border-b border-theme-border px-3 py-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-theme-accent/30 bg-theme-accent/15 text-theme-accent-light">
                <PanelRightOpen className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-semibold text-theme-text" title={preview.relativeDirectory}>
                  Live preview · {preview.relativeDirectory}
                </p>
                <p className="text-[10px] text-emerald-400">Host verified · {preview.files} files</p>
              </div>
              <button
                type="button"
                onClick={() => setPreviewRefresh(value => value + 1)}
                className="rounded-lg p-2 text-theme-text-muted transition hover:bg-theme-surface-hover hover:text-theme-text"
                title="Reload preview"
              >
                <RefreshCw className="h-4 w-4" />
              </button>
              <a
                href={previewAccess.url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-lg p-2 text-theme-text-muted transition hover:bg-theme-surface-hover hover:text-theme-text"
                title="Open preview in a new tab"
              >
                <ExternalLink className="h-4 w-4" />
              </a>
              <button
                type="button"
                onClick={() => setPreview(null)}
                className="rounded-lg p-2 text-theme-text-muted transition hover:bg-theme-surface-hover hover:text-theme-text"
                title="Close preview"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <iframe
              key={`${preview.siteId}-${previewRefresh}`}
              src={previewAccess.url}
              title="Interactive Pixel preview"
              sandbox={previewAccess.sandbox}
              data-preview-route={previewAccess.route}
              referrerPolicy="no-referrer"
              className="min-h-0 flex-1 border-0 bg-white"
            />
          </aside>
        )}
      </div>
    </div>
  )
}
