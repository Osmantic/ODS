import { useCallback, useEffect, useRef, useState } from 'react'
import { readConversations, saveConversation, SELECT_EVENT, DELETE_EVENT, deleteConversation, isConversationDeleted } from '../lib/pixelConversations'
import ReactMarkdown from 'react-markdown'
import {usePixelAutoScroll} from '../lib/usePixelAutoScroll'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { Link } from 'react-router-dom'
import PixelAdvice from '../components/PixelAdvice.jsx'
import PixelMascot from '../components/PixelMascot.jsx'
import UserAvatar from '../components/UserAvatar'
import {useLocalProfile} from '../lib/localProfile'
import { pixelHeaderPose, pixelReplyPose } from '../lib/pixelMascotState'
import PixelComposerTools from '../components/PixelComposerTools'
import PixelTextFileInput from '../components/PixelTextFileInput'
import PixelDraftPreview from '../components/PixelDraftPreview'
import PixelDictation from '../components/PixelDictation'
import PixelCommandSearch, { OPEN_PIXEL_SEARCH } from '../components/PixelCommandSearch'
import PixelConversationImport from '../components/PixelConversationImport'
import PixelSelectionActions from '../components/PixelSelectionActions'
import PixelTaskFiles from '../components/PixelTaskFiles'
import PixelTaskActivity from '../components/PixelTaskActivity'
import PixelTurnNavigation from '../components/PixelTurnNavigation'
import PixelSnapshotChanges from '../components/PixelSnapshotChanges'
import PixelPreviewViewport from '../components/PixelPreviewViewport'
import PixelPreviewHistory from '../components/PixelPreviewHistory'
import { parseTaskActivity, parseTaskActivityFrame } from '../lib/pixelTaskActivity'
import MetalMetricIcon from '../components/MetalMetricIcon'
import PanelResizeHandle from '../components/PanelResizeHandle.jsx'
import PixelHandoffApproval from '../components/PixelHandoffApproval.jsx'
import PixelProviderScopes from '../components/PixelProviderScopes.jsx'
import { usePortalIdentity } from '../contexts/PortalIdentityContext'
import {usePixelSendKey, shouldSendMessage} from '../lib/usePixelSendKey'
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Code2,
  Copy,
  ExternalLink,
  Loader2,
  Plus,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Send,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  X,
} from 'lucide-react'

const MARKDOWN_COMPONENTS = {
  p: ({ children }) => <p className="break-words [&:not(:first-child)]:mt-3">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="break-words">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ children, className = '' }) => <code className={`rounded bg-theme-bg/70 px-1 py-0.5 font-mono text-[13px] text-theme-text ${className}`}>{children}</code>,
  pre: ({ children }) => <pre className="my-2 overflow-x-auto rounded border border-theme-border bg-theme-bg/70 [&>code]:block [&>code]:p-2">{children}</pre>,
  table: ({ children }) => (
    <div role="region" aria-label="Scrollable table" tabIndex={0} className="my-3 max-w-full overflow-x-auto rounded border border-theme-border">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  th: ({ children, style }) => <th scope="col" style={style} className="border-b border-theme-border bg-theme-bg/70 px-3 py-2 font-semibold">{children}</th>,
  td: ({ children, style }) => <td style={style} className="border-b border-theme-border px-3 py-2 align-top [overflow-wrap:anywhere]">{children}</td>,
  a: ({ href, children }) => {
    const safe = typeof href === 'string' && /^https?:\/\//i.test(href)
    // The viewer may reach ODS through a remote host or SSH forward. A local
    // snapshot URL in a reply must use the same authenticated dashboard relay
    // as the preview pane, including after the pane is closed or chat restored.
    const snapshot = safe && href.match(/^http:\/\/(site-[a-f0-9]{24})\.localhost:([1-9][0-9]{0,4})\/\1\/$/)
    const target = snapshot && Number(snapshot[2]) <= 65535
      ? `/pixel-preview/${snapshot[1]}/`
      : href
    return safe
      ? <a href={target} target="_blank" rel="noopener noreferrer" className="text-theme-accent-light underline">{children}</a>
      : <span>{children}</span>
  },
}

const MAX_INPUT_LEN = 16 * 1024
const MAX_REQUEST_MESSAGES = 50
const MAX_TOTAL_MESSAGE_BYTES = 256 * 1024
// Visible history is independent of the model's per-request context budget.
const MAX_STORED_MESSAGES = 2000
const MAX_STORED_MESSAGE_BYTES = 4 * 1024 * 1024
const CHAT_STORAGE_KEY = 'ods.pixel.chat.v1'
const SAFE_CHAT_ID = /^[A-Za-z0-9_-]{1,128}$/
const STOPPED_NOTICE = 'Stopped by you. Workspace changes completed before cancellation were preserved.'
const MODEL_SWITCH_DETAIL = 'Model switch in progress; Pixel will be ready when activation completes'
const CLEAN_CONTEXT_RECOVERY_REASON = 'operations-unavailable-zero-submissions'
const CLEAN_CONTEXT_RECOVERY_NOTICE = 'The first attempt did not reach the Operations Broker, and the host verified that no work was submitted. Retrying once with a clean context…'
const CLEAN_CONTEXT_RECOVERY_FAILED = 'Automatic recovery was attempted once, but Pixel again did not reach the Operations Broker. The host verified that no Operations work was submitted. Check any other work before continuing; this does not confirm that other tools had no effects.'
const STATUS_POLL_MS = 3000
const OPS_STATUS_POLL_MS = 3000
const OPS_TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'rejected'])
const OPS_APPROVAL_RECEIPT = /^Pixel prepared the exact (ods\.extensions\.(?:install|enable|disable|remove)) plan for extension ([a-z0-9](?:[a-z0-9_-]|\.(?=[a-z0-9])){0,63}), but external approval is required\. No lifecycle change was executed\. Job: (ops-[0-9]{13}-[a-f0-9]{12})\. Plan SHA-256: ([a-f0-9]{64})\.$/
const OPS_HOST_COMMAND_APPROVAL_RECEIPT = /^Pixel prepared a protected ODS host command plan, but external approval is required\. No command was executed\. Job: (ops-[0-9]{13}-[a-f0-9]{12})\. Plan SHA-256: ([a-f0-9]{64})\.$/
let fallbackChatSequence = 0

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

export function resolvePreviewAccess(preview) {
  if (!preview) return null
  return {
    url: `/pixel-preview/${preview.siteId}/`,
    frameUrl: `/pixel-preview/${preview.siteId}/__ods_view__.html`,
    // A loopback dashboard can be an SSH forward to another machine. Use its
    // authenticated relay rather than assuming the viewer hosts the snapshot.
    // Keep it opaque even though it shares the Dashboard URL.
    sandbox: 'allow-scripts allow-forms allow-downloads',
    route: 'private-dashboard',
  }
}


function ApprovalCommand({command}) {
  const [state, setState] = useState('idle')
  const active = useRef(true)
  const pending = useRef(false)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  async function copy() {
    if (pending.current) return
    pending.current = true
    setState('pending')
    try {
      if (!globalThis.navigator?.clipboard?.writeText) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(command)
      if (active.current) setState('copied')
    } catch {
      if (active.current) setState('error')
    } finally { pending.current = false }
  }
  return <>
    <button type="button" onClick={copy} disabled={state === 'pending'}
      className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-1.5 text-xs font-medium text-amber-200 transition hover:bg-amber-400/15">
      {state === 'copied' ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {state === 'copied' ? 'Copied' : state === 'pending' ? 'Copying…' : 'Copy secure approval command'}
    </button>
    {state === 'error' && <div>
      <p role="alert" className="mt-2 text-xs">Clipboard access failed. Select and copy the verified command manually.</p>
      <textarea aria-label="Secure approval command" readOnly value={command}
        className="mt-2 w-full rounded border border-theme-border bg-theme-bg p-2 font-mono text-xs" />
    </div>}
  </>
}

export function OperationsApprovalCard({ content }) {
  const receipt = parseApprovalReceipt(content)
  const [projection, setProjection] = useState(null)
  const [verification, setVerification] = useState(receipt ? 'loading' : 'absent')

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
              <ApprovalCommand key={projection.approvalCommand} command={projection.approvalCommand} />
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

function workingDetail(elapsedSeconds, displayName) {
  if (elapsedSeconds < 15) return 'Starting the owner-agent turn'
  if (elapsedSeconds < 60) return `${displayName} is working with the active model`
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

function messagePublication(message) {
  const validate = preview => parseVerifiedPreviewFrame({choices:[{finish_reason:'stop'}],pixel:{schemaVersion:1,preview}})
  if (message?.role !== 'assistant') return {}
  const publication = validate(message.publication)
  const beforePublication = validate(message.beforePublication)
  return publication ? {publication, beforePublication:beforePublication?.relativeDirectory === publication.relativeDirectory ? beforePublication : null} : {}
}

function messageOutcome(message) {
  return message.role === 'assistant' && ['done', 'error', 'stopped'].includes(message.status)
    ? {status:message.status} : {}
}

function retainedResult(events) {
  let content = ''
  let preview = null
  let task = null
  let done = false
  let failed = false
  for (const line of events.split('\n')) {
    if (!line.startsWith('data:')) continue
    const payload = line.slice(5).trim()
    if (payload === '[DONE]') { done = true; break }
    try {
      const frame = JSON.parse(payload)
      if (frame?.error) { failed = true; continue }
      if (failed) continue
      if (isCleanContextRecoveryFrame(frame)) {
        content = 'Pixel did not start this attempt. Send your message again to continue.'
        failed = true
        continue
      }
      const candidate = parseVerifiedPreviewFrame(frame)
      if (candidate) preview = candidate
      const candidateTask = parseTaskActivityFrame(frame)
      if (candidateTask) task = candidateTask
      const text = frame?.choices?.[0]?.delta?.content
      if (typeof text === 'string') content += text
    } catch { /* The same bounded SSE boundary applies to retained results. */ }
  }
  return { content, preview: done && !failed ? preview : null, task, done, failed }
}

function loadStoredChat(selected) {
  try {
    const stored = selected || JSON.parse(globalThis.localStorage?.getItem(CHAT_STORAGE_KEY) || 'null')
    if (
      stored?.schema !== 1
      || !SAFE_CHAT_ID.test(stored.chatId || '')
      || isConversationDeleted(stored.chatId)
      || !Array.isArray(stored.messages)
      || stored.messages.length > MAX_STORED_MESSAGES
    ) return null

    let totalBytes = 0
    const messages = stored.messages.map((message) => {
      if (
        !message
        || !['user', 'assistant'].includes(message.role)
        || typeof message.content !== 'string'
        || (message.role === 'user' && message.content.length > MAX_INPUT_LEN)
      ) throw new Error('invalid stored Pixel message')
      totalBytes += new TextEncoder().encode(message.content).byteLength
      if (totalBytes > MAX_STORED_MESSAGE_BYTES) throw new Error('stored Pixel chat is too large')
      const task = message.role === 'assistant' && parseTaskActivity(message.task, message.task?.runId)
      return { role: message.role, content: message.content, ...messageOutcome(message), ...(task ? {task} : {}), ...messagePublication(message) }
    })
    // Reuse the terminal marker validator for persisted metadata. Never infer
    // an iframe URL from conversation text, and always use the authenticated
    // snapshot relay when restoring a preview.
    let preview = null
    try {
      preview = parseVerifiedPreviewFrame({
        choices: [{ finish_reason: 'stop' }],
        pixel: { schemaVersion: 1, preview: stored.preview },
      })
    } catch {
      // A damaged preview must not discard an otherwise valid conversation.
    }
    return {
      chatId: stored.chatId, messages, preview,
      contextStart: Number.isInteger(stored.contextStart) && stored.contextStart >= 0 && stored.contextStart <= messages.length ? stored.contextStart : 0,
      workspaceOpen: stored.workspaceOpen !== false && (stored.workspaceOpen === true || Boolean(preview)),
      draft: typeof stored.draft === 'string' ? stored.draft.slice(0, MAX_INPUT_LEN) : '',
      requestId: SAFE_CHAT_ID.test(stored.requestId || '') ? stored.requestId : null,
      interrupted: stored.inFlight === true || stored.interrupted === true,
    }
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
    const { role } = messages[index]
    // The transport's per-message cap is not a transcript storage limit.
    // Bound only the copy sent to the model; keep the complete reply in chat.
    const omission = '\n[Earlier response shortened for model context.]'
    const original = messages[index].content
    const content = original.length > MAX_INPUT_LEN
      ? original.slice(0, MAX_INPUT_LEN - omission.length) + omission : original
    const size = encoder.encode(content).byteLength
    if (bytes + size > budget) break
    selected.unshift({ role, content })
    bytes += size
  }
  while (selected[0]?.role === 'assistant') selected.shift()
  return selected
}

export default function Pixel({ systemStatus = null }) {
  const profile = useLocalProfile()
  const { displayName } = usePortalIdentity()
  const [initialChat] = useState(loadStoredChat)
  const pendingImport = useRef(null)
  const sendKey = usePixelSendKey()

  const [status, setStatus] = useState('loading')
  const [statusDetail, setStatusDetail] = useState('')
  const [messages, setMessages] = useState(() => initialChat?.messages || [])
  const [input, setInput] = useState(() => initialChat?.draft || '')
  const [persistenceError, setPersistenceError] = useState('')
  const [sending, setSending] = useState(false)
  const [interrupted, setInterrupted] = useState(() => initialChat?.interrupted || false)
  const [stopping, setStopping] = useState(false)
  const [stopError, setStopError] = useState('')
  const [restoredActivity, setRestoredActivity] = useState(() => initialChat?.interrupted ? 'checking' : 'idle')
  const [activityRefresh, setActivityRefresh] = useState(0)
  const [workingElapsedSeconds, setWorkingElapsedSeconds] = useState(0)
  const [agentRuntime, setAgentRuntime] = useState(null)
  const [modelSupport, setModelSupport] = useState(null)
  const [preview, setPreview] = useState(() => initialChat?.preview || null)
  const [previewRefresh, setPreviewRefresh] = useState(0)
  const [previewCollapsed, setPreviewCollapsed] = useState(false)
  const [previewWidth, setPreviewWidth] = useState(440)
  const [previewTab, setPreviewTab] = useState('preview')
  const [workspaceOpen, setWorkspaceOpen] = useState(() => initialChat?.workspaceOpen || false)
  useEffect(() => { setPreviewTab('preview') }, [preview?.siteId])

  const abortRef = useRef(null)
  const restoredActivityRef = useRef(restoredActivity)
  const chatIdRef = useRef(initialChat?.chatId || makeChatId())
  const contextStartRef = useRef(initialChat?.contextStart || 0)
  const requestIdRef = useRef(initialChat?.requestId || null)
  const inputRef = useRef(null)
  const scrollRef = useRef(null)
  const chatScroll = usePixelAutoScroll(messages, chatIdRef.current, scrollRef)

  const activeModel = agentRuntime?.model || systemStatus?.inference?.loadedModel || systemStatus?.model?.name || ''
  const activeContext = formatContext(
    agentRuntime?.contextLength || systemStatus?.inference?.contextSize || systemStatus?.model?.contextLength
  )
  const previewAccess = resolvePreviewAccess(preview)
  const restoredActive = interrupted && !sending && restoredActivity === 'active'
  const restoredChecking = interrupted && !sending && restoredActivity === 'checking'
  const updateRestoredActivity = useCallback((value) => {
    restoredActivityRef.current = value
    setRestoredActivity(value)
  }, [])

  useEffect(() => {
    if (!interrupted || sending) return undefined
    const chatId = chatIdRef.current
    const requestId = requestIdRef.current
    let controller = null
    let disposed = false
    let timer = null
    async function checkActivity() {
      controller = new AbortController()
      const deadline = globalThis.setTimeout(() => controller.abort(), 15000)
      let state = 'unknown'
      try {
        if (requestId) {
          const resultResponse = await fetch('/api/pixel/chat/result', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: chatId, request_id: requestId }), signal: controller.signal,
          })
          const result = await resultResponse.json()
          if (disposed || chatIdRef.current !== chatId || requestIdRef.current !== requestId) return
          if (resultResponse.ok && result && Object.keys(result).sort().join(',') === 'events,state'
            && typeof result.events === 'string' && result.events.length <= 8 * 1024 * 1024) {
            if (result.state === 'active') {
              updateRestoredActivity('active')
              timer = globalThis.setTimeout(checkActivity, 2000)
              return
            }
            if (['complete', 'interrupted', 'cancelled'].includes(result.state)) {
              const recovered = retainedResult(result.events)
              const successful = result.state === 'complete' && recovered.done && !recovered.failed
              setMessages(previous => {
                const publication = successful ? recovered.preview : null
                const before = [...previous].reverse().find(message => message.publication?.relativeDirectory === publication?.relativeDirectory)?.publication || null
                return replaceLastAssistant(previous, {
                content: result.state === 'cancelled' ? stoppedContent(recovered.content)
                  : recovered.content || (successful ? 'Completed without a text response.' : 'Pixel could not complete the response. Check saved work before continuing.'),
                status: result.state === 'cancelled' ? 'stopped' : successful ? 'done' : 'error',
                ...(recovered.task ? {task:recovered.task} : {}),
                ...(publication ? {publication,beforePublication:before} : {}),
              })})
              if (successful && recovered.preview) {
                setPreview(recovered.preview); setPreviewRefresh(0)
                setWorkspaceOpen(true); setPreviewCollapsed(false)
              }
              requestIdRef.current = null
              setInterrupted(false)
              updateRestoredActivity('terminal')
              return
            }
          }
        }
        const response = await fetch('/api/pixel/chat/activity', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chat_id: chatId }), signal: controller.signal,
        })
        const data = await response.json()
        if (response.ok && data && Object.keys(data).length === 1
          && ['active', 'terminal', 'unknown'].includes(data.state)) state = data.state
      } catch {
        // A failed lookup or edge restart is not evidence that work finished.
      } finally { globalThis.clearTimeout(deadline) }
      if (disposed || chatIdRef.current !== chatId) return
      updateRestoredActivity(state)
      if (state !== 'terminal') timer = globalThis.setTimeout(checkActivity, 2000)
    }
    checkActivity()
    return () => {
      disposed = true
      controller?.abort()
      if (timer !== null) globalThis.clearTimeout(timer)
    }
  }, [interrupted, sending, activityRefresh, updateRestoredActivity])

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
    const field = inputRef.current
    if (!field) return
    field.style.height = 'auto'
    field.style.height = `${Math.min(field.scrollHeight, 160)}px`
  }, [input])

  useEffect(() => {
    try {
      const storedMessages = messages.map(message => {
        const task = message.role === 'assistant' && parseTaskActivity(message.task, message.task?.runId)
        return {role: message.role, content: message.content, ...messageOutcome(message), ...(task ? {task} : {}), ...messagePublication(message)}
      })
      // Report storage limits without silently trimming previous turns.
      if (storedMessages.length > MAX_STORED_MESSAGES || storedMessages.reduce((total, message) => total + new TextEncoder().encode(message.content).byteLength, 0) > MAX_STORED_MESSAGE_BYTES) throw new Error('stored Pixel chat is too large')
      saveConversation({
        schema: 1,
        chatId: chatIdRef.current,
        requestId: requestIdRef.current,
        inFlight: sending,
        interrupted,
        draft: input,
        messages: storedMessages,
        contextStart: contextStartRef.current,
        preview,
        workspaceOpen,
      })
      setPersistenceError('')
    } catch {
      // Conversation persistence is a convenience; chat remains usable when
      // storage is unavailable, full, or blocked by the browser.
      setPersistenceError('Your browser could not save this conversation. Keep this page open to avoid losing it.')
    }
  }, [messages, preview, workspaceOpen, sending, interrupted, input])

  const sendMessage = useCallback(async () => {
    const trimmed = input.trim()
    if (!trimmed || sending || abortRef.current || restoredActive || restoredChecking || status !== 'available' || trimmed.length > MAX_INPUT_LEN) return

    const userMessage = { role: 'user', content: trimmed }
    const originalContextStart = contextStartRef.current
    // Local assistant messages carry UI-only status metadata. Keep the API
    // boundary exact so a completed or failed first turn cannot make the next
    // request fail the dashboard API's extra="forbid" contract.
    const conversation = [
      ...boundedHistory(messages.slice(originalContextStart), trimmed),
      userMessage,
    ]
    const visibleConversation = [...messages, userMessage]
    setMessages([...visibleConversation, { role: 'assistant', content: '', status: 'streaming' }])
    setInput('')
    setSending(true)
    setInterrupted(false)
    updateRestoredActivity('idle')
    setStopping(false)
    setStopError('')

    const controller = new AbortController()
    abortRef.current = controller
    // Stop releases the UI before the old reader necessarily settles. Only
    // this generation may update the response, workspace, or sending state.
    const isCurrentTurn = () => !controller.signal.aborted && abortRef.current === controller
    let latestAssistantText = ''

    async function streamAttempt(chatId, attemptConversation) {
      let reader
      let assistantText = ''
      let receivedDone = false
      let receivedError = false
      let recoveryEligible = false
      let verifiedPreview = null
      let taskActivity = null

      try {
        const requestId = makeChatId()
        requestIdRef.current = requestId
        // Commit the attempt identity before the POST can start tool work.
        // A page close before React's persistence effect must still recover it.
        try {
          saveConversation({
            schema: 1, chatId, requestId, inFlight: true, interrupted: false,
            messages: [...visibleConversation, { role: 'assistant', content: '' }], preview,
            draft: '', contextStart: contextStartRef.current, workspaceOpen,
          })
        } catch {
          requestIdRef.current = null
          throw new Error('chat-recovery-storage-unavailable')
        }
        const response = await fetch('/api/pixel/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chat_id: chatId, request_id: requestId, messages: attemptConversation }),
          signal: controller.signal,
        })
        if (!isCurrentTurn()) return { kind: 'obsolete' }
        if (response.status === 409) {
          requestIdRef.current = null
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
          requestIdRef.current = null
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
          if (!isCurrentTurn()) return { kind: 'obsolete' }
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
                  content: assistantText ? `${assistantText}\n\n_Pixel could not complete the response._` : 'Pixel could not complete the response.',
                  status: 'error',
                }))
                continue
              }
              // Error is terminal for this reply. Late deltas must not turn a
              // failed response back into an apparently running/successful one.
              if (receivedError) continue
              if (isCleanContextRecoveryFrame(frame)) recoveryEligible = true
              const candidatePreview = parseVerifiedPreviewFrame(frame)
              if (candidatePreview) verifiedPreview = candidatePreview
              const candidateTask = parseTaskActivityFrame(frame)
              if (candidateTask) {
                taskActivity = candidateTask
                setMessages(previous => replaceLastAssistant(previous, {task:candidateTask}))
              }
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
          taskActivity,
        }
      } finally {
        reader?.releaseLock?.()
      }
    }

    function finishAttempt(attempt, recovered = false) {
      // An acknowledged Stop or page disposal can close a reader normally.
      // Its late close must not overwrite the explicit cancellation outcome.
      if (!isCurrentTurn()) return
      if (attempt.receivedError) { setInterrupted(true); return }
      if (attempt.receivedDone) requestIdRef.current = null
      if (attempt.receivedDone) {
        const previousPublication = [...messages].reverse().find(message => message.publication?.relativeDirectory === attempt.verifiedPreview?.relativeDirectory)?.publication || preview
        if (attempt.verifiedPreview) {
          setPreview(attempt.verifiedPreview)
          setWorkspaceOpen(true)
          setPreviewCollapsed(false)
          setPreviewRefresh(0)
        }
        setMessages(previous => replaceLastAssistant(previous, {
          status: 'done',
          ...(attempt.taskActivity ? {task: attempt.taskActivity} : {}),
          ...(attempt.verifiedPreview ? {publication:attempt.verifiedPreview, beforePublication:previousPublication?.relativeDirectory === attempt.verifiedPreview.relativeDirectory ? previousPublication : null} : {}),
          ...(recovered ? { recovered: true } : {}),
        }))
        return
      }
      const content = attempt.assistantText
        ? `${attempt.assistantText}\n\n_Response interrupted._`
        : 'Connection interrupted'
      setInterrupted(true)
      setMessages(previous => replaceLastAssistant(previous, { content, status: 'error' }))
    }

    try {
      let attempt = await streamAttempt(chatIdRef.current, conversation)
      if (!isCurrentTurn()) return
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
        contextStartRef.current = visibleConversation.length - 1
        latestAssistantText = ''
        setMessages([
          ...visibleConversation,
          {
            role: 'assistant',
            content: CLEAN_CONTEXT_RECOVERY_NOTICE,
            status: 'recovering',
          },
        ])

        attempt = await streamAttempt(retryChatId, [userMessage])
        if (!isCurrentTurn()) return
        if (attempt.kind === 'switching') {
          contextStartRef.current = messages.length
          setMessages(messages)
          setInput(trimmed)
          setStatus('switching')
          setStatusDetail(`${attempt.detail}. The clean-context request is preserved.`)
          return
        }
        if (attempt.kind === 'adaptive') {
          contextStartRef.current = messages.length
          setMessages(messages)
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
      if (isCurrentTurn() && error?.name !== 'AbortError') {
        const storageFailed = error?.message === 'chat-recovery-storage-unavailable'
        setInterrupted(!storageFailed)
        if (storageFailed) setInput(trimmed)
        setMessages(previous => replaceLastAssistant(previous, {
          content: storageFailed ? 'Could not save the request for recovery. No task was started. Check browser storage and try again.' : latestAssistantText || 'Request failed',
          status: 'error',
        }))
      }
    } finally {
      if (isCurrentTurn()) {
        setSending(false)
        setStopping(false)
        setStopError('')
        abortRef.current = null
      }
    }
  }, [input, messages, preview, workspaceOpen, sending, status, restoredActive, restoredChecking, updateRestoredActivity])

  const stopStreaming = useCallback(async () => {
    const controller = abortRef.current
    const chatId = chatIdRef.current
    const requestId = requestIdRef.current
    const restored = !controller && interrupted
      && ['active', 'unknown'].includes(restoredActivityRef.current)
    if ((!controller && !restored) || stopping) return

    setStopping(true)
    setStopError('')
    try {
      const response = await fetch('/api/pixel/chat/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, ...(requestId ? { request_id: requestId } : {}) }),
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
      if (chatIdRef.current !== chatId || requestIdRef.current !== requestId || abortRef.current !== controller
        || (restored && !['active', 'unknown'].includes(restoredActivityRef.current))) return
      controller?.abort()
      abortRef.current = null
      requestIdRef.current = null
      setMessages(previous => replaceLastAssistant(previous, {
        content: stoppedContent(previous.at(-1)?.content),
        status: 'stopped',
      }))
      setSending(false)
      setInterrupted(false)
      updateRestoredActivity('terminal')
    } catch {
      // Keep the live stream attached and Stop retryable. Claiming success
      // without an exact acknowledgement could leave tools or inference active.
      if (chatIdRef.current === chatId && abortRef.current === controller) {
        setStopError(restored
          ? 'Stop was not confirmed. This chat may still have work in progress; check its activity or retry the stop request.'
          : 'Stop was not confirmed. Pixel is still connected; retry Stop.')
        if (restored) setActivityRefresh(value => value + 1)
      }
    } finally {
      setStopping(false)
    }
  }, [stopping, interrupted, updateRestoredActivity])

  const startNewChat = useCallback(() => {
    if (sending || restoredActive || restoredChecking || stopping) return
    chatIdRef.current = makeChatId()
    requestIdRef.current = null
    contextStartRef.current = 0
    setMessages([])
    setPreview(null)
    setWorkspaceOpen(false)
    setPreviewRefresh(0)
    setInput('')
    setInterrupted(false)
    updateRestoredActivity('idle')
    inputRef.current?.focus?.()
  }, [sending, restoredActive, restoredChecking, stopping, updateRestoredActivity])

  useEffect(() => {
    const remove = event => {
      const {chatId, complete} = event.detail
      if (sending || restoredActive || restoredChecking || stopping) {
        complete('Stop the current task before deleting a conversation.')
        return
      }
      try {
        deleteConversation(chatId)
        if (chatId === chatIdRef.current) startNewChat()
        complete('')
      } catch (error) { complete(error.message || 'Could not delete this conversation.') }
    }
    window.addEventListener(DELETE_EVENT, remove)
    return () => window.removeEventListener(DELETE_EVENT, remove)
  }, [sending, restoredActive, restoredChecking, stopping, startNewChat])

  const insertComposerText = useCallback(text => {
    if (sending || restoredActive || restoredChecking || stopping) return
    setInput(value => value === '/' ? text : `${value}${value && !value.endsWith(' ') && !value.endsWith('\n') ? ' ' : ''}${text}`)
    inputRef.current?.focus?.()
  }, [sending, restoredActive, restoredChecking, stopping])

  useEffect(() => {
    window.addEventListener('ods:pixel-new-task', startNewChat)
    return () => window.removeEventListener('ods:pixel-new-task', startNewChat)
  }, [startNewChat])

  useEffect(() => {
    const select = event => {
      if (sending || restoredActive || restoredChecking || stopping) {
        setStopError('Stop the current task before switching conversations.')
        return
      }
      const chat = loadStoredChat(readConversations().find(item => item.chatId === event.detail))
      if (!chat || chat.chatId === chatIdRef.current) return
      chatIdRef.current = chat.chatId
      requestIdRef.current = chat.requestId
      contextStartRef.current = chat.contextStart
      setMessages(chat.messages)
      setPreview(chat.preview)
      setWorkspaceOpen(chat.workspaceOpen)
      setPreviewRefresh(0)
      setInput(chat.draft)
      setStopError('')
      setInterrupted(chat.interrupted)
      updateRestoredActivity(chat.interrupted ? 'checking' : 'idle')
      // Two interrupted chats have identical flags; the ref-only identity
      // change must still dispose the previous poll and check the new chat.
      setActivityRefresh(value => value + 1)
    }
    window.addEventListener(SELECT_EVENT, select)
    return () => window.removeEventListener(SELECT_EVENT, select)
  }, [sending, restoredActive, restoredChecking, stopping, updateRestoredActivity])

  const inputOver = input.length > MAX_INPUT_LEN
  const inputEmpty = !input.trim()
  const isDisabled = sending || restoredActive || restoredChecking || stopping || status !== 'available'
  const workingElapsed = formatElapsed(workingElapsedSeconds)
  const statusLabel = stopping
    ? 'Stopping'
    : sending
      ? 'Working'
    : restoredActive
      ? 'Working in this chat'
    : restoredChecking
      ? 'Checking previous work'
    : interrupted && restoredActivity === 'unknown'
      ? 'Activity unknown'
    : status === 'available'
      ? 'Available'
      : status === 'switching'
        ? 'Switching model...'
      : status === 'loading'
        ? 'Connecting...'
        : 'Degraded'

  return (
    <div className="pixel-chat flex flex-col overflow-hidden text-theme-text">
      <div className="pixel-chat-preview-layout flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="pixel-chat-column flex min-h-0 min-w-0 flex-1 flex-col">
      {persistenceError && <p role="alert" className="px-6 py-2 text-sm text-amber-300">{persistenceError}</p>}
      <header className="pixel-chat-header">
        <div className="pixel-chat-identity">
        <div className="flex h-9 w-9 items-center justify-center text-theme-accent-light">
          <PixelMascot interactive activityKey={input} name={displayName} state={pixelHeaderPose({sending, stopping, restoredActive, restoredChecking, interrupted, restoredActivity, status, task:messages.at(-1)?.task})} />
        </div>
        <div className="min-w-0">
          <h1 className="text-base font-semibold leading-tight truncate max-w-[40vw]" title={displayName}>{displayName}</h1>
          <p className="text-[11px] text-theme-text-muted">Your local ODS owner agent</p>
        </div>
        </div>

        <div className="pixel-chat-header-actions">
          <button type="button" aria-label="Search Pixel" title="Search conversations · Ctrl+K" className="pixel-metal-control p-2" onClick={() => window.dispatchEvent(new Event(OPEN_PIXEL_SEARCH))}><Search size={16}/></button>
          <details className="pixel-chat-options"><summary aria-label="Chat options">•••</summary><div className="pixel-chat-options-menu">
            <PixelConversationImport key={chatIdRef.current} disabled={sending || restoredActive || restoredChecking || stopping} onImport={record => {
              if (sending || restoredActive || restoredChecking || stopping) throw new Error('Active task')
              if (pendingImport.current?.record !== record) pendingImport.current = {record, chatId:makeChatId()}
              const imported = {...record, chatId:pendingImport.current.chatId}
              saveConversation(imported)
              pendingImport.current = null
              window.dispatchEvent(new CustomEvent(SELECT_EVENT, {detail:imported.chatId}))
            }}/>
            <label className="block p-2 text-xs">Send shortcut<select className="mt-1 block w-full rounded border border-theme-border bg-theme-bg p-2" aria-label="Send shortcut" value={sendKey.mode} onChange={event => sendKey.change(event.target.value)}><option value="enter">Enter to send</option><option value="mod-enter">Ctrl/⌘+Enter to send</option></select></label>
            {sendKey.error && <p role="alert" className="p-2 text-xs">{sendKey.error}</p>}

            <PixelTurnNavigation messages={messages} onNavigate={index => {
              const row = scrollRef.current?.parentElement?.querySelector(`[data-pixel-message-index="${index}"]`)
              row?.scrollIntoView?.({block:'start', behavior:'auto'})
              row?.focus?.({preventScroll:true})
            }}/>
            <PixelAdvice canInsert={!sending} onInsert={text => setInput(current => current ? `${current}\n\n${text}` : text)} />
            <PixelHandoffApproval label="Approvals" />
            <PixelProviderScopes chatId={chatIdRef.current} sending={sending} />
          </div></details>
          {activeModel && (
            <div
              className="pixel-chat-model hidden min-w-0 items-center rounded-md border border-theme-border px-2 py-1.5 font-mono text-[10px] text-theme-text-muted sm:flex"
              title={activeModel}
            >
              <span className="truncate text-theme-text-secondary">{activeModel}</span>
            </div>
          )}
          <Link
            to="/models"
            className="hidden rounded-lg px-2.5 py-1.5 text-xs font-medium text-theme-text-muted transition hover:bg-theme-surface-hover hover:text-theme-text sm:inline-flex"
          >
            Change model
          </Link>
          <button type="button" aria-label="Workspace" aria-expanded={workspaceOpen} onClick={() => { setWorkspaceOpen(value => !value); setPreviewCollapsed(false) }} className="inline-flex items-center gap-1.5 bg-transparent px-2.5 py-1.5 text-xs text-theme-text-secondary hover:text-theme-text">
            <PanelRightOpen size={14}/><span>Workspace</span>
          </button>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={startNewChat}
              disabled={sending || restoredActive || restoredChecking || stopping}
              className="inline-flex items-center gap-1.5 rounded-none border-0 bg-transparent px-2.5 py-1.5 text-xs font-medium text-theme-text-secondary transition hover:text-theme-text disabled:cursor-not-allowed disabled:opacity-50"
              title="Start a new chat"
            >
              <Plus className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">New chat</span>
            </button>
          )}
          <span
            aria-live="polite"
            title={modelSupport?.detail || undefined}
            className={`inline-flex items-center gap-1.5 text-[11px] font-medium ${
            sending
              ? 'text-theme-accent-light'
              : status === 'available'
              ? 'text-emerald-400'
              : 'text-amber-300'
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
      </header>
      <div role="region" aria-label="Conversation messages" tabIndex={-1} onScroll={chatScroll.onScroll} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-6">
        {interrupted && !sending && (
          <div role="status" className="mx-auto w-full max-w-5xl rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
            {restoredActivity === 'active'
              ? 'The previous request is still active in this chat. Your saved conversation and preview are preserved. You can stop that work below; its live response cannot be reattached.'
              : restoredActivity === 'terminal'
                ? 'The previous request is no longer active. Its final response was not recovered; check the saved files and results before continuing.'
                : restoredActivity === 'checking'
                  ? 'Checking whether this chat’s previous request is still active. Your saved conversation and preview are preserved.'
                  : 'Completion was not confirmed. This chat’s activity is unknown. Your request and partial response are saved; check its results before continuing. You can attempt to stop previous work in this chat without resending it.'}
            {restoredActivity === 'unknown' && (
              <>
                <button type="button" className="ml-2 underline" disabled={stopping} onClick={() => {
                  updateRestoredActivity('checking')
                  setActivityRefresh(value => value + 1)
                }}>Check activity again</button>
                <button type="button" className="ml-2 underline" disabled={stopping}
                  onClick={stopStreaming}>Try Stop previous work</button>
              </>
            )}
          </div>
        )}
        {status === 'loading' && messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center text-theme-text-muted">
            <Loader2 className="mb-3 h-8 w-8 animate-spin" />
            <p>Connecting to {displayName}...</p>
          </div>
        )}
        {status === 'unavailable' && messages.length === 0 && (
          <div className="pixel-welcome mx-auto text-theme-text-muted">
            <PixelMascot className="pixel-welcome-character" />
            <h2>What do you want to work on?</h2>
            <p className="pixel-welcome-description">Start a private task, explore an idea, or create something new.</p>
            <div className="pixel-offline-notice" role="status">
            <p className="font-medium text-theme-text">{displayName} is currently unavailable</p>
            {statusDetail && <p className="mt-1 text-sm">{statusDetail}</p>}
            <p className="mt-4 text-xs">Your other ODS applications remain available while the agent reconnects.</p>
            </div>
          </div>
        )}
        {status === 'switching' && messages.length === 0 && (
          <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center text-center text-theme-text-muted">
            <Loader2 className="mb-4 h-9 w-9 animate-spin text-theme-accent-light" />
            <p className="font-medium text-theme-text">{displayName} is switching models</p>
            <p className="mt-1 text-sm">Your draft is safe. {displayName} will reconnect automatically when activation completes.</p>
          </div>
        )}
        {status === 'available' && messages.length === 0 && (
          <div className="pixel-welcome mx-auto text-theme-text-muted">
            <div>
              <PixelMascot interactive activityKey={input} name={displayName} className="pixel-welcome-character" />
              <h2>What do you want to work on?</h2>
              <p className="pixel-welcome-description">Start a private task, explore an idea, or create something new.</p>
            </div>


          </div>
        )}
        {messages.map((message, index) => (
          <div key={index} data-pixel-message-index={index} tabIndex={-1} data-pixel-response={message.role === 'assistant' ? '' : undefined} className={`mx-auto flex min-w-0 w-full max-w-5xl ${message.role === 'user' ? 'justify-end gap-2' : 'justify-start'}`}>
            {message.role === 'assistant' && <PixelMascot state={pixelReplyPose(message, sending && index === messages.length - 1)} settled={message.status !== 'streaming'} className="pixel-reply-character" />}
            <div className={`min-w-0 max-w-[min(85%,48rem)] rounded-2xl px-4 py-3 text-sm leading-6 [overflow-wrap:anywhere] ${
              message.role === 'user'
                ? 'border border-theme-border bg-theme-card text-theme-text'
                : message.status === 'error'
                  ? 'border border-red-500/25 bg-red-500/10 text-red-200'
                  : message.status === 'stopped'
                    ? 'pixel-stopped-response bg-transparent text-theme-text-secondary'
                  : 'bg-transparent text-theme-text-secondary'
            }`}>
              {message.status === 'stopped' && (
                <div role="status" className="mb-2 inline-flex items-center gap-2 text-xs font-medium text-theme-text-muted">
                  <Square className="h-3 w-3" />
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
                  {message.publication && <PixelSnapshotChanges preview={message.publication} before={message.beforePublication} onPreview={() => {setPreview(message.publication);setWorkspaceOpen(true);setPreviewCollapsed(false);setPreviewTab('preview')}}/>}
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={MARKDOWN_COMPONENTS}>{message.content}</ReactMarkdown>
                  <OperationsApprovalCard content={message.content} />
                </>
              ) : (
                <span className="break-words whitespace-pre-wrap">{message.content}</span>
              )}
              {message.status === 'streaming' && !message.content && (
                <span role="status" className="inline-flex items-start gap-2 text-theme-text-muted">
                  <span>
                    <span className="pixel-working-label block">{workingDetail(workingElapsedSeconds, displayName)}</span>
                    <span className="mt-0.5 block text-xs text-theme-text-muted/80">
                      {workingElapsed} elapsed · You can stop safely at any time.
                    </span>
                  </span>
                </span>
              )}
            </div>
            {message.role === 'user' && <UserAvatar profile={profile} className="pixel-user-character"/>}
          </div>
        ))}
        <div ref={scrollRef} />
      </div>

      <div className="pixel-composer px-4 py-3 sm:px-6">
        {chatScroll.showLatest && <div className="mb-2 text-center"><button type="button" onClick={chatScroll.jumpToLatest} className="rounded border border-theme-border px-3 py-1 text-xs">Jump to latest</button></div>}
        <div className="mx-auto max-w-5xl">
          <div className="pixel-composer-row">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (shouldSendMessage(event, sendKey.mode)) {
                event.preventDefault()
                sendMessage()
              }
            }}
            placeholder={status === 'available'
              ? `Message ${displayName}...`
              : status === 'switching'
                ? 'Waiting for model switch...'
                : `${displayName} is unavailable`}
            disabled={isDisabled}
            rows={1}
            className={`pixel-composer-input min-h-11 flex-1 resize-none rounded-xl border bg-theme-card px-4 py-2.5 text-sm text-theme-text outline-none transition placeholder:text-theme-text-muted/70 disabled:opacity-50 ${
              inputOver ? 'border-red-400' : 'border-theme-border'
            }`}
          />
          <div className="pixel-composer-actions">
          <PixelDictation disabled={isDisabled} conversationId={chatIdRef.current} onInsert={insertComposerText}/>
          {sending || restoredActive ? (
            <button
              onClick={stopStreaming}
              disabled={stopping}
              className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-theme-border bg-theme-surface text-theme-text transition hover:bg-theme-surface-hover disabled:cursor-wait disabled:opacity-70"
              title={stopping ? 'Stopping' : 'Stop'}
            >
              {stopping ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={isDisabled || inputOver || inputEmpty}
              className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-theme-accent text-white transition hover:bg-theme-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
              title="Send"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
          </div>
          </div>
          {stopError && <p role="alert" className="mt-1.5 px-1 text-xs text-amber-300">{stopError}</p>}
          <div className="pixel-composer-secondary">
            <PixelComposerTools input={input} disabled={isDisabled} onInsert={insertComposerText}>
              <PixelTextFileInput key={`file-input-${chatIdRef.current}`} input={input} disabled={isDisabled} limit={MAX_INPUT_LEN} onInsert={insertComposerText}/>
              <PixelDraftPreview key={`draft-preview-${chatIdRef.current}`} input={input}/>
            </PixelComposerTools>
            <div className="pixel-composer-limits">
              {activeContext && <span title="Model context window shared by instructions, conversation, tools, and reply">{activeContext}</span>}
              <span className={inputOver ? 'text-red-400' : ''} title="Characters in this message, not tokens or context usage">{input.length.toLocaleString()} / {MAX_INPUT_LEN.toLocaleString()} chars</span>
            </div>
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-3 px-1 text-[10px] text-theme-text-muted/70">
            <span>{stopping ? 'Waiting for exact cancellation acknowledgement' : restoredActive ? 'Earlier work is active in this chat; Stop targets only this chat.' : sending ? `${displayName} is using the active ODS model and tools · ${workingElapsed} elapsed` : sendKey.mode === 'mod-enter' ? 'Ctrl/⌘+Enter to send • Enter for a new line' : 'Enter to send • Shift+Enter for a new line'}</span>
          </div>
        </div>
        {inputOver && (
          <p className="mx-auto mt-1 max-w-5xl px-1 text-xs text-red-400">
            Message too long (max {MAX_INPUT_LEN.toLocaleString()} characters)
          </p>
        )}
      </div>
        </div>

        <PixelCommandSearch onInsert={insertComposerText} onNewTask={startNewChat}/>
        <PixelSelectionActions disabled={isDisabled} conversationId={chatIdRef.current} onInsert={insertComposerText}/>
        {workspaceOpen && (
          <aside aria-label="Preview panel" style={{'--preview-width':`${previewWidth}px`}} className={`pixel-preview-panel ${previewCollapsed ? 'is-collapsed' : ''} flex shrink-0 flex-col border-theme-border bg-theme-bg`}>
            {!previewCollapsed && <PanelResizeHandle width={previewWidth} onResize={setPreviewWidth} label="Resize preview panel" container=".pixel-chat-preview-layout" minimum={240} />}
            <div className="pixel-workspace-toolbar flex items-center gap-2 border-b border-theme-border px-3 py-2.5">
              <nav className="pixel-preview-tabs" aria-label="Preview views">
                <button type="button" aria-pressed={previewTab === 'activity'} onClick={() => setPreviewTab('activity')}>Activity</button>
                <button type="button" aria-pressed={previewTab === 'files'} onClick={() => setPreviewTab('files')}>Files</button>
                <button type="button" aria-pressed={previewTab === 'preview'} onClick={() => setPreviewTab('preview')}>Preview</button>
                <button type="button" aria-pressed={previewTab === 'changes'} onClick={() => setPreviewTab('changes')}>Changes</button>
              </nav>
              <div className="pixel-workspace-actions">
              <button type="button" onClick={() => setPreviewCollapsed(value => !value)} title={previewCollapsed ? 'Expand preview' : 'Collapse preview'} className="rounded-lg p-2 text-theme-text-muted">
                <MetalMetricIcon icon={previewCollapsed ? PanelRightOpen : PanelRightClose} size={16}/>
              </button>
              <button
                type="button"
                onClick={() => setPreviewRefresh(value => value + 1)}
                className="rounded-lg p-2 text-theme-text-muted transition hover:bg-theme-surface-hover hover:text-theme-text"
                title="Reload preview"
                disabled={!preview}
              >
                <MetalMetricIcon icon={RefreshCw} size={16} />
              </button>
              {preview && <a
                href={previewAccess.url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-lg p-2 text-theme-text-muted transition hover:bg-theme-surface-hover hover:text-theme-text"
                title="Open preview in a new tab"
              >
                <MetalMetricIcon icon={ExternalLink} size={16} />
              </a>}
              <button
                type="button"
                onClick={() => setWorkspaceOpen(false)}
                className="rounded-lg p-2 text-theme-text-muted transition hover:bg-theme-surface-hover hover:text-theme-text"
                title="Close preview"
              >
                <MetalMetricIcon icon={X} size={16} />
              </button>
              </div>
            </div>
            {!previewCollapsed && preview && <PixelPreviewHistory previews={messages.map(message => messagePublication(message).publication).filter(Boolean)} selected={preview} onSelect={publication => {setPreview(publication); setPreviewRefresh(0)}}/>}
            {preview && <PixelPreviewViewport
              key={`preview-${preview.siteId}-${previewRefresh}`}
              access={previewAccess}
              title={`Interactive ${displayName} preview`}
              hidden={previewCollapsed || previewTab !== 'preview'}
            />}
            {!previewCollapsed && preview && previewTab === 'files' && <PixelTaskFiles key={`files-${preview.siteId}-${previewRefresh}`} preview={preview}/>}
            {!previewCollapsed && preview && previewTab === 'changes' && <div className="pixel-workspace-changes"><PixelSnapshotChanges key={`${preview.siteId}-${previewRefresh}`} preview={preview} before={[...messages].reverse().find(message=>message.publication?.siteId === preview.siteId)?.beforePublication || null} onPreview={()=>setPreviewTab('preview')}/></div>}
            {!previewCollapsed && previewTab === 'activity' && <PixelTaskActivity key={chatIdRef.current} messages={messages} sending={sending} elapsed={workingElapsed}/>}
            {!previewCollapsed && !preview && previewTab !== 'activity' && <section className="pixel-workspace-empty">
              <Code2 size={24}/><h2>{previewTab === 'files' ? 'No published files yet' : 'No preview published yet'}</h2>
              <p>Saving HTML in the agent workspace does not publish it here. Pixel must publish the site and ODS must verify the result.</p>
              <button type="button" disabled={isDisabled} onClick={() => insertComposerText('Publique o site que voce criou nesta conversa no preview do ODS. Inspecione os arquivos existentes, preserve o projeto e use pixel_ods_workspace_preview para a pasta que contem index.html. Nao apenas descreva o arquivo.')}>Ask Pixel to publish</button>
              <small>Adds a request to your message. Review it before sending.</small>
            </section>}
          </aside>
        )}
      </div>
    </div>
  )
}
