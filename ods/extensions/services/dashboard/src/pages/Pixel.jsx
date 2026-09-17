import { useCallback, useEffect, useRef, useState } from 'react'
import PixelConversationRecovery from '../components/PixelConversationRecovery'
import { readConversations, saveConversation, createConversationWriter, SELECT_EVENT, DELETE_EVENT, deleteConversation, isConversationDeleted } from '../lib/pixelConversations'
import {usePixelAutoScroll} from '../lib/usePixelAutoScroll'
import { Link } from 'react-router-dom'
import PixelAdvice from '../components/PixelAdvice.jsx'
import PixelMascot from '../components/PixelMascot.jsx'
import UserAvatar from '../components/UserAvatar'
import {useLocalProfile} from '../lib/localProfile'
import { pixelHeaderPose, pixelReplyPose } from '../lib/pixelMascotState'
import PixelComposerTools from '../components/PixelComposerTools'
import PortalAgentDock from '../components/PortalAgentDock'
import {ACTIVE_TEAMS,agentCommand,teamMetadata,teamProjectTasks,teamRequest,teamSummary,usePortalTeams} from '../lib/portalTeams'
import PixelTextFileInput from '../components/PixelTextFileInput'
import PixelDraftPreview from '../components/PixelDraftPreview'
import PixelKeyboardHelp from '../components/PixelKeyboardHelp'
import PixelDictation from '../components/PixelDictation'
import PixelCommandSearch, { OPEN_PIXEL_SEARCH } from '../components/PixelCommandSearch'
import PixelConversationImport from '../components/PixelConversationImport'
import { appendComposerText } from '../lib/pixelComposerText'
import PixelSelectionActions from '../components/PixelSelectionActions'
import PixelQuestions from '../components/PixelQuestions'
import PortalGoalPlan from '../components/PortalGoalPlan'
import {goalCommand,continueGoal} from '../lib/portalGoal'
import PortalContextRing from '../components/PortalContextRing'
import {compactCommand,CONTEXT_REQUEST_ID,historySnapshot,usePortalContext} from '../lib/portalContext'
import PortalModelSelector from '../components/PortalModelSelector'
import PortalAgentActivity from '../components/PortalAgentActivity'
import PortalStreamingText from '../components/PortalStreamingText'
import PortalResponseActions from '../components/PortalResponseActions'
import PortalResponseError from '../components/PortalResponseError'
import {publicationDisplayText} from '../lib/publicationDisplay'
import {isQuestionAnswer, parseQuestionsFrame, questionMetadata} from '../lib/pixelQuestions'
import PixelTurnNavigation from '../components/PixelTurnNavigation'
import PixelSnapshotChanges from '../components/PixelSnapshotChanges'
import PortalWorkspace from '../components/PortalWorkspace'
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
  Loader2,
  Plus,
  PanelRightOpen,
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
      <table className="pixel-response-table w-full border-collapse text-left text-sm">{children}</table>
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

export function latestProjectPublication(publication,messages) {
  return [...messages].reverse().map(messagePublication).find(item=>item.publication?.relativeDirectory===publication?.relativeDirectory)?.publication || publication
}

function messageOutcome(message) {
  return message.role === 'assistant' && ['done', 'error', 'stopped'].includes(message.status)
    ? {status:message.status} : {}
}

function retainedResult(events) {
  let content = ''
  let preview = null
  let task = null
  let questions = null
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
      const candidateQuestions = parseQuestionsFrame(frame)
      if (candidateQuestions) questions = candidateQuestions
      const text = frame?.choices?.[0]?.delta?.content
      if (typeof text === 'string') content += text
    } catch { /* The same bounded SSE boundary applies to retained results. */ }
  }
  return { content, preview: done && !failed ? preview : null, task, questions: done && !failed ? questions : null, done, failed }
}

function loadStoredChat(selected) {
  try {
    let stored = selected || JSON.parse(globalThis.localStorage?.getItem(CHAT_STORAGE_KEY) || 'null')
    if (!selected && stored?.schema === 1 && SAFE_CHAT_ID.test(stored.chatId || '') && stored.persistenceVersion !== 2) {
      // Older clients committed the library before the active pointer. Restore
      // that authority before autosave can migrate an older pointer over it.
      stored = readConversations().find(chat => chat.chatId === stored.chatId) || stored
    }
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
      return { role: message.role, content: message.content, ...messageOutcome(message), ...(task ? {task} : {}), ...messagePublication(message), ...questionMetadata(message), ...teamMetadata(message) }
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
      persistenceSnapshot: stored,
      chatId: stored.chatId, messages, preview,
      contextStart: Number.isInteger(stored.contextStart) && stored.contextStart >= 0 && stored.contextStart <= messages.length ? stored.contextStart : 0,
      compactionRequestId: CONTEXT_REQUEST_ID.test(stored.compactionRequestId || '') ? stored.compactionRequestId : null,
      workspaceOpen: stored.workspaceOpen !== false && (stored.workspaceOpen === true || Boolean(preview)),
      // The send limit must not truncate unsent text when restoring a draft.
      // The composer keeps sending disabled until the user shortens it.
      draft: typeof stored.draft === 'string' ? stored.draft : '',
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
  const conversationWriter = useRef(null)
  if (!conversationWriter.current) conversationWriter.current = createConversationWriter(initialChat?.persistenceSnapshot)
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
  const [contextRuntime, setContextRuntime] = useState(null)
  const [modelSupport, setModelSupport] = useState(null)
  const [modelSwitching,setModelSwitching]=useState(false)
  const [modelStatusRefresh,setModelStatusRefresh]=useState(0)
  const [preview, setPreview] = useState(() => initialChat?.preview || null)
  const [previewRefresh, setPreviewRefresh] = useState(0)
  const [previewCollapsed, setPreviewCollapsed] = useState(false)
  const [previewWidth, setPreviewWidth] = useState(640)
  const [workspaceRequest, setWorkspaceRequest] = useState(null)
  const handleWorkspaceRequest=useCallback(request=>setWorkspaceRequest(current=>current===request?null:current),[])
  const [workspaceExpanded, setWorkspaceExpanded] = useState(false)
  const [workspaceOpen, setWorkspaceOpen] = useState(() => initialChat?.workspaceOpen || false)
  function openPublication(publication, kind, path=null) {
    const current=latestProjectPublication(publication,messages)
    setPreview(current);setWorkspaceOpen(true);setPreviewCollapsed(false)
    setWorkspaceRequest({chatId:chatIdRef.current,siteId:current.siteId,kind,path})
  }

  const abortRef = useRef(null)
  const stopRequestRef = useRef(null)
  const restoredActivityRef = useRef(restoredActivity)
  const chatIdRef = useRef(initialChat?.chatId || makeChatId())
  useEffect(() => { setWorkspaceRequest(null); setWorkspaceExpanded(false) }, [chatIdRef.current])
  const contextStartRef = useRef(initialChat?.contextStart || 0)
  const compactionRequestRef = useRef(initialChat?.compactionRequestId || null)
  const requestIdRef = useRef(initialChat?.requestId || null)
  const inputRef = useRef(null)
  const scrollRef = useRef(null)
  const chatScroll = usePixelAutoScroll(messages, chatIdRef.current, scrollRef)
  const command=agentCommand(input)
  const goalDraft=goalCommand(input)
  const teams=usePortalTeams(chatIdRef.current,Boolean(command || goalDraft || messages.some(m=>m.teamId || m.teamRequestId)))
  function openAgents(selection=null) {
    teams.select(selection)
    setWorkspaceOpen(true);setPreviewCollapsed(false)
    setWorkspaceRequest({chatId:chatIdRef.current,kind:'agents'})
  }
  useEffect(()=>{
    if(!teams.selected)return
    setWorkspaceOpen(true);setPreviewCollapsed(false)
    setWorkspaceRequest({chatId:chatIdRef.current,kind:'agents'})
  },[teams.selected])
  const teamAttempt=useRef(null)
  useEffect(()=>{
    if(!teams.teams.length)return
    setMessages(previous=>{
      let changed=false
      const next=previous.map(message=>{
        if(!message.teamId && !message.teamRequestId)return message
        const team=teams.teams.find(t=>t.id===message.teamId || t.request_id===message.teamRequestId)
        if(!team)return message
        const content=teamSummary(team)
        if(team.mode==='goal') {
          const agent=team.agents[0], active=['queued','running'].includes(team.status)
          const observed=parseTaskActivity(agent?.activity,agent?.activity?.runId)
          const plan=observed?.goal?.steps.length ? observed.goal : agent?.goal_plan || observed?.goal || message.task?.goal
          const task=observed ? {...observed,goal:plan || null} : message.task ? {...message.task} : undefined
          if(task?.goal && ['failed','cancelled','interrupted'].includes(team.status))task.goal={...task.goal,status:'blocked',summary:(agent?.error || team.notice || 'This goal was stopped. Review the saved work before continuing.').slice(0,300)}
          const next={...message,teamId:team.id,goalMode:true,content,task,status:active?'streaming':'done',
            questions:agent?.questions || undefined,goalNotice:agent?.error || team.notice || '',goalState:team.status}
          if(JSON.stringify(message)===JSON.stringify(next))return message
          changed=true;return next
        }
        const projectTasks=teamProjectTasks(team,message.projectTasks)
        if(message.content===content && message.teamId===team.id
          && JSON.stringify(message.projectTasks || [])===JSON.stringify(projectTasks))return message
        changed=true;return {...message,teamId:team.id,content,...(projectTasks.length ? {projectTasks} : {})}
      })
      return changed ? next : previous
    })
  },[teams.teams])

  const activeModel = agentRuntime?.model || (contextRuntime ? '' : systemStatus?.inference?.loadedModel || systemStatus?.model?.name || '')
  const activeContext = agentRuntime ? formatContext(agentRuntime.contextLength) : ''
  const currentPreview=preview ? latestProjectPublication(preview,messages) : null
  const previewAccess = resolvePreviewAccess(currentPreview)
  const restoredActive = interrupted && !sending && restoredActivity === 'active'
  const restoredChecking = interrupted && !sending && restoredActivity === 'checking'
  const contextCapacity=Number(contextRuntime?.contextLength || systemStatus?.inference?.contextSize || systemStatus?.model?.contextLength) || null
  const contextControl=usePortalContext({chatId:chatIdRef.current,
    runtimeIdentity:{model:contextRuntime?.model || activeModel,source:contextRuntime?.source || '',routeFingerprint:contextRuntime?.routeFingerprint},capacity:contextCapacity,
    initialRequestId:compactionRequestRef.current,
    blocked:sending || modelSwitching || stopping || teams.busy || (interrupted && restoredActivity!=='terminal') || status!=='available',
    onPendingChange:(id,chatId)=>{
      if(chatId!==chatIdRef.current)throw new Error('The conversation changed before compaction could be saved.')
      historySnapshot(messages)
      conversationWriter.current({schema:1,chatId,requestId:requestIdRef.current,inFlight:sending,interrupted,draft:input,
        messages,contextStart:contextStartRef.current,compactionRequestId:id,preview,workspaceOpen})
      compactionRequestRef.current=id
    },
  })
  const compactConversation=useCallback(async()=>{
    const accepted=await contextControl.compact()
    if(accepted && (compactCommand(input) || input==='/'))setInput(value=>value===input?'':value)
  },[contextControl.compact,input])
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
                ...(successful && recovered.questions ? {questions:recovered.questions} : {}),
                ...(publication ? {publication,beforePublication:before} : {}),
              })})
              if (successful && recovered.preview) {
                setPreview(recovered.preview); setPreviewRefresh(0)
                setWorkspaceOpen(true); setPreviewCollapsed(false)
              }
              requestIdRef.current = null
              setInterrupted(false)
              updateRestoredActivity('terminal')
              void contextControl.refresh(true)
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
        if (stopped) return
        const runtime = data?.runtime
        const runtimeKeys = runtime && typeof runtime === 'object' && !Array.isArray(runtime)
          ? Object.keys(runtime).sort().join('\n')
          : ''
        const validRemoteRuntime = [
          ['contextLength', 'maxTokens', 'model', 'reasoning', 'source'].join('\n'),
          ['contextLength', 'maxTokens', 'model', 'reasoning', 'routeFingerprint', 'source'].join('\n'),
        ].includes(runtimeKeys)
          && runtime.source === 'remote-provider'
          && (runtime.routeFingerprint === undefined || typeof runtime.routeFingerprint === 'string'
            && runtime.routeFingerprint.length === 64 && /^[a-f0-9]{64}$/.test(runtime.routeFingerprint))
          && Number.isInteger(runtime.maxTokens)
          && runtime.maxTokens >= 1
          && runtime.maxTokens <= runtime.contextLength
          && typeof runtime.reasoning === 'boolean'
          && runtime.contextLength >= 4096
        const validLocalRuntime = runtimeKeys === ['contextLength', 'model', 'source'].join('\n')
          && runtime.source === 'local-switchboard'
        const confirmedRuntime = (validRemoteRuntime || validLocalRuntime)
          && typeof runtime.model === 'string'
          && runtime.model.length > 0
          && runtime.model.length <= 256
          && Number.isInteger(runtime.contextLength)
          && runtime.contextLength >= 1
          && runtime.contextLength <= 10_000_000
            ? runtime
            : null
        // Keep the last confirmed identity only for measured context. Model
        // activation needs a current source proof; a missing projection must
        // never authorize switching based on the previous local runtime.
        setAgentRuntime(confirmedRuntime)
        if (confirmedRuntime) setContextRuntime(confirmedRuntime)
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
        if (!stopped && error?.name !== 'AbortError') {
          setAgentRuntime(null)
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
  }, [modelStatusRefresh])

  useEffect(() => () => {
    abortRef.current?.abort()
    stopRequestRef.current?.abort()
    stopRequestRef.current = null
  }, [])

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
        return {role: message.role, content: message.content, ...messageOutcome(message), ...(task ? {task} : {}), ...messagePublication(message), ...questionMetadata(message), ...teamMetadata(message)}
      })
      // Report storage limits without silently trimming previous turns.
      if (storedMessages.length > MAX_STORED_MESSAGES || storedMessages.reduce((total, message) => total + new TextEncoder().encode(message.content).byteLength, 0) > MAX_STORED_MESSAGE_BYTES) throw new Error('stored Pixel chat is too large')
      conversationWriter.current({
        schema: 1,
        chatId: chatIdRef.current,
        requestId: requestIdRef.current,
        inFlight: sending,
        interrupted,
        draft: input,
        messages: storedMessages,
        contextStart: contextStartRef.current,
        compactionRequestId: compactionRequestRef.current,
        preview,
        workspaceOpen,
      })
      setPersistenceError('')
    } catch (error) {
      // Conversation persistence is a convenience; chat remains usable when
      // storage is unavailable, full, or blocked by the browser.
      setPersistenceError(error?.code === 'conversation-changed' ? error.message
        : 'Your browser could not save this conversation. Keep this page open to avoid losing it.')
    }
  }, [messages, preview, workspaceOpen, sending, interrupted, input])

  const sendMessage = useCallback(async (answerOverride) => {
    const trimmed = (typeof answerOverride === 'string' ? answerOverride : input).trim()
    if(compactCommand(trimmed)){await compactConversation();return}
    if(contextControl.busy || contextControl.historyUnknown)return
    if (!trimmed || sending || modelSwitching || abortRef.current || restoredActive || restoredChecking || status !== 'available' || trimmed.length > MAX_INPUT_LEN) return
    if(teams.busy)return
    if(goalCommand(trimmed) && !goalCommand(trimmed).task) { setStopError('Describe the goal you want to complete.'); return }
    const requestedGoal=goalCommand(trimmed)
    const teamCommand=agentCommand(trimmed) || requestedGoal
    if(teamCommand) {
      if(!teamCommand.task || teamCommand.task.length>8000){setStopError('Describe what you want the team to do, in up to 8,000 characters.');return}
      const signature=JSON.stringify([chatIdRef.current,trimmed])
      if(teamAttempt.current?.signature!==signature)teamAttempt.current={signature,id:makeChatId(),context:messages.filter(m=>!m.teamRequestId).slice(-4).map(m=>`${m.role}: ${m.content.slice(0,450)}`).join('\n').slice(-1800)}
      const requestId=teamAttempt.current.id
      const context=teamAttempt.current.context
      setMessages(previous=>previous.some(m=>m.teamRequestId===requestId) ? previous : [...previous,{role:'user',content:trimmed},{role:'assistant',content:requestedGoal?'Preparing your goal…':'Preparing the agent team…',teamRequestId:requestId,...(requestedGoal?{goalMode:true}:{}),status:'done'}])
      setStopError('')
      try {
        const team=await teams.start({request_id:requestId,task:teamCommand.task,context,...(requestedGoal?{mode:'goal'}:{})})
        if(team){setInput('');teamAttempt.current=null}
      } catch(e){setStopError(e.message)}
      return
    }

    const userMessage = { role: 'user', content: trimmed }
    const originalContextStart = contextStartRef.current
    let fullHistory
    try {fullHistory=historySnapshot([...messages.slice(originalContextStart),userMessage])}
    catch(error){setStopError(error.message);return}
    // Local assistant messages carry UI-only status metadata. Keep the API
    // boundary exact so a completed or failed first turn cannot make the next
    // request fail the dashboard API's extra="forbid" contract.
    const conversation = [
      ...boundedHistory(messages.slice(originalContextStart), trimmed),
      userMessage,
    ]
    const visibleConversation = [...messages, userMessage]
    setMessages([...visibleConversation, { role: 'assistant', content: '', status: 'streaming', revealResponse:makeChatId() }])
    if (typeof answerOverride !== 'string') setInput('')
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

    async function streamAttempt(chatId, attemptConversation, snapshot) {
      let reader
      let assistantText = ''
      let receivedDone = false
      let receivedError = false
      let recoveryEligible = false
      let verifiedPreview = null
      let taskActivity = null
      let questions = null

      try {
        const requestId = makeChatId()
        const body=JSON.stringify({chat_id:chatId,request_id:requestId,messages:attemptConversation,history_snapshot:snapshot})
        if(new TextEncoder().encode(body).byteLength>8*1024*1024)throw new Error('history-request-too-large')
        requestIdRef.current = requestId
        // Commit the attempt identity before the POST can start tool work.
        // A page close before React's persistence effect must still recover it.
        try {
          conversationWriter.current({
            schema: 1, chatId, requestId, inFlight: true, interrupted: false,
            messages: [...visibleConversation, { role: 'assistant', content: '' }], preview,
            draft: typeof answerOverride === 'string' ? input : '', contextStart: contextStartRef.current, compactionRequestId:compactionRequestRef.current, workspaceOpen,
          })
        } catch {
          requestIdRef.current = null
          throw new Error('chat-recovery-storage-unavailable')
        }
        const response = await fetch('/api/pixel/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
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
              const candidateQuestions = parseQuestionsFrame(frame)
              if (candidateQuestions) questions = candidateQuestions
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
          questions,
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
          ...(attempt.questions && attempt.receivedDone && !attempt.receivedError ? {questions:attempt.questions} : {}),
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
      let attempt = await streamAttempt(chatIdRef.current, conversation, fullHistory)
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
        contextStartRef.current = originalContextStart
        latestAssistantText = ''
        setMessages([
          ...visibleConversation,
          {
            role: 'assistant',
            content: CLEAN_CONTEXT_RECOVERY_NOTICE,
            status: 'recovering',
            revealResponse:makeChatId(),
          },
        ])

        // The new session hydrates earlier messages as inert history. Only the
        // current user message remains the active task in the legacy request.
        attempt = await streamAttempt(retryChatId, [userMessage], fullHistory)
        if (!isCurrentTurn()) return
        if (attempt.kind === 'switching') {
          contextStartRef.current = originalContextStart
          setMessages(messages)
          setInput(trimmed)
          setStatus('switching')
          setStatusDetail(`${attempt.detail}. The clean-context request is preserved.`)
          return
        }
        if (attempt.kind === 'adaptive') {
          contextStartRef.current = originalContextStart
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
        const historyTooLarge=error?.message==='history-request-too-large'
        setInterrupted(!storageFailed && !historyTooLarge)
        if (storageFailed || historyTooLarge) setInput(trimmed)
        setMessages(previous => replaceLastAssistant(previous, {
          content: historyTooLarge?'The encoded conversation exceeds the 8 MB request limit. No task was started. Export this conversation before starting a new chat.':storageFailed ? 'Could not save the request for recovery. No task was started. Check browser storage and try again.' : latestAssistantText || 'Request failed',
          status: 'error',
        }))
      }
    } finally {
      if (isCurrentTurn()) {
        setSending(false)
        setStopping(false)
        setStopError('')
        abortRef.current = null
        void contextControl.refresh(true)
      }
    }
  }, [input, messages, preview, workspaceOpen, sending, modelSwitching, status, restoredActive, restoredChecking, updateRestoredActivity, teams.busy, teams.start,compactConversation,contextControl.busy,contextControl.historyUnknown,contextControl.refresh])

  const stopStreaming = useCallback(async () => {
    const controller = abortRef.current
    const chatId = chatIdRef.current
    const requestId = requestIdRef.current
    const restored = !controller && interrupted
      && ['active', 'unknown'].includes(restoredActivityRef.current)
    if ((!controller && !restored) || stopping || stopRequestRef.current) return

    // Bound the acknowledgement independently of the live chat stream.
    // A deadline is uncertainty, never permission to claim the task stopped.
    const stopRequest = new AbortController()
    stopRequestRef.current = stopRequest
    const timeout = setTimeout(() => stopRequest.abort(), 15000)
    setStopping(true)
    setStopError('')
    try {
      const response = await fetch('/api/pixel/chat/cancel', {
        method: 'POST',
        signal: stopRequest.signal,
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
      if (stopRequest.signal.aborted || !response?.ok || !payload || Object.keys(payload).length !== 1 || payload.aborted !== true) {
        throw new Error('cancellation was not acknowledged')
      }

      // A normal terminal response may win the cancellation race. Do not
      // rewrite that completed answer as owner-stopped.
      if (stopRequestRef.current !== stopRequest || chatIdRef.current !== chatId || requestIdRef.current !== requestId || abortRef.current !== controller
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
      if (stopRequestRef.current === stopRequest && chatIdRef.current === chatId && abortRef.current === controller) {
        setStopError(restored
          ? 'Stop was not confirmed. This chat may still have work in progress; check its activity or retry the stop request.'
          : 'Stop was not confirmed. Pixel is still connected; retry Stop.')
        if (restored) setActivityRefresh(value => value + 1)
      }
    } finally {
      clearTimeout(timeout)
      if (stopRequestRef.current === stopRequest) {
        stopRequestRef.current = null
        setStopping(false)
      }
    }
  }, [stopping, interrupted, updateRestoredActivity])

  const startNewChat = useCallback(() => {
    if (sending || restoredActive || restoredChecking || stopping || teams.launching || contextControl.busy) return
    chatIdRef.current = makeChatId()
    requestIdRef.current = null
    contextStartRef.current = 0
    compactionRequestRef.current = null
    setMessages([])
    setPreview(null)
    setWorkspaceOpen(false)
    setPreviewRefresh(0)
    setInput('')
    setInterrupted(false)
    updateRestoredActivity('idle')
    inputRef.current?.focus?.()
  }, [sending, restoredActive, restoredChecking, stopping, updateRestoredActivity, teams.launching,contextControl.busy])

  useEffect(() => {
    const remove = async event => {
      const {chatId, complete} = event.detail
      if(chatId===chatIdRef.current && teams.busy){complete('Stop the agent team before deleting this conversation.');return}
      if (sending || restoredActive || restoredChecking || stopping || contextControl.busy) {
        complete('Stop the current task before deleting a conversation.')
        return
      }
      try {
        const stored=readConversations().find(item=>item.chatId===chatId)
        if(stored?.messages?.some(message=>message.teamId || message.teamRequestId)) {
          const result=await teamRequest('list',{chat_id:chatId})
          if(!Array.isArray(result.teams))throw new Error('Could not verify the team status before deleting this conversation.')
          if(result.teams.some(team=>ACTIVE_TEAMS.has(team.status))){complete('Stop the agent team before deleting this conversation.');return}
        }
        deleteConversation(chatId)
        if (chatId === chatIdRef.current) startNewChat()
        complete('')
      } catch (error) { complete(error.message || 'Could not delete this conversation.') }
    }
    window.addEventListener(DELETE_EVENT, remove)
    return () => window.removeEventListener(DELETE_EVENT, remove)
  }, [sending, restoredActive, restoredChecking, stopping, startNewChat, teams.busy,contextControl.busy])

  const insertComposerText = useCallback(text => {
    if (sending || restoredActive || restoredChecking || stopping || contextControl.busy) return
    setInput(value => {
      const mode=agentCommand(text)?'agents':goalCommand(text)?'goal':null
      const task=(agentCommand(value) || goalCommand(value))?.task ?? (value==='/'?'':value)
      return mode ? `/${mode} ${task}` : appendComposerText(value, text)
    })
    inputRef.current?.focus?.()
  }, [sending, restoredActive, restoredChecking, stopping,contextControl.busy])

  useEffect(() => {
    window.addEventListener('ods:pixel-new-task', startNewChat)
    return () => window.removeEventListener('ods:pixel-new-task', startNewChat)
  }, [startNewChat])

  useEffect(() => {
    const select = event => {
      if (sending || restoredActive || restoredChecking || stopping || teams.launching || contextControl.busy) {
        setStopError('Stop the current task before switching conversations.')
        return
      }
      const chat = loadStoredChat(readConversations().find(item => item.chatId === event.detail))
      if (!chat || chat.chatId === chatIdRef.current) return
      conversationWriter.current = createConversationWriter(chat.persistenceSnapshot)
      chatIdRef.current = chat.chatId
      requestIdRef.current = chat.requestId
      contextStartRef.current = chat.contextStart
      compactionRequestRef.current = chat.compactionRequestId
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
  }, [sending, restoredActive, restoredChecking, stopping, updateRestoredActivity, teams.launching,contextControl.busy])

  const inputOver = input.length > MAX_INPUT_LEN
  const inputEmpty = !(command?.task ?? goalDraft?.task ?? input).trim()
  const isDisabled = sending || modelSwitching || restoredActive || restoredChecking || stopping || teams.busy || contextControl.busy || contextControl.historyUnknown || status !== 'available'
  const workingElapsed = formatElapsed(workingElapsedSeconds)
  const statusLabel = contextControl.busy ? (contextControl.phase==='unknown'?'Checking context':'Compacting') : stopping
    ? 'Stopping'
    : teams.busy
      ? (teams.teams[0]?.mode==='goal' ? 'Goal active' : 'Agent team')
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
      <div className={`pixel-chat-preview-layout flex min-h-0 flex-1 flex-col lg:flex-row ${workspaceExpanded && workspaceOpen && !previewCollapsed ? 'is-workspace-expanded' : ''}`}>
        <div className="pixel-chat-column flex min-h-0 min-w-0 flex-1 flex-col">
      {persistenceError && <PixelConversationRecovery error={persistenceError} chatId={chatIdRef.current} messages={messages} draft={input}/>}
      <header className="pixel-chat-header">
        <div className="pixel-chat-identity">
        <div className="flex h-9 w-9 items-center justify-center text-theme-accent-light">
          <PixelMascot interactive activityKey={input} name={displayName} state={pixelHeaderPose({sending, stopping, restoredActive, restoredChecking, interrupted, restoredActivity, status, task:messages.at(-1)?.task})} />
        </div>
        <div className="min-w-0">
          <h1 className="text-base font-semibold leading-tight truncate max-w-[40vw]" title={displayName}>{displayName}</h1>
          <p className="text-[11px] text-theme-text-muted">Your local ODS owner agent</p>
        </div>
        <PortalAgentDock controller={teams} onOpen={openAgents}/>
        </div>

        <div className="pixel-chat-header-actions">
          <button type="button" aria-label="Search Pixel" title="Search conversations · Ctrl+K" className="pixel-metal-control p-2" onClick={() => window.dispatchEvent(new Event(OPEN_PIXEL_SEARCH))}><Search size={16}/></button>
          <details className="pixel-chat-options"><summary aria-label="Chat options">•••</summary><div className="pixel-chat-options-menu">
            <PixelConversationImport key={chatIdRef.current} disabled={sending || restoredActive || restoredChecking || stopping || contextControl.busy} onImport={record => {
              if (sending || restoredActive || restoredChecking || stopping || contextControl.busy) throw new Error('Active task')
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
            <PixelHandoffApproval label="Approvals" />
            <details className="pixel-chat-options-advanced"><summary>Advanced tools</summary><div>
              <PixelAdvice canInsert={!sending && !contextControl.busy} onInsert={text => setInput(current => current ? `${current}\n\n${text}` : text)} />
              <PixelProviderScopes chatId={chatIdRef.current} sending={sending} />
            </div></details>
          </div></details>
          <button type="button" aria-label="Workspace" aria-expanded={workspaceOpen} onClick={() => { setWorkspaceOpen(value => !value); setPreviewCollapsed(false) }} className="inline-flex items-center gap-1.5 bg-transparent px-2.5 py-1.5 text-xs text-theme-text-secondary hover:text-theme-text">
            <PanelRightOpen size={14}/><span>Workspace</span>
          </button>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={startNewChat}
              disabled={sending || restoredActive || restoredChecking || stopping || contextControl.busy}
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
        {messages.map((message, index) => {
          if (isQuestionAnswer(messages,index)) return null
          const displayedContent=message.role==='assistant' ? publicationDisplayText(message.content,message.publication) : message.content
          return (
          <div key={index} data-pixel-message-index={index} tabIndex={-1} data-pixel-response={message.role === 'assistant' ? '' : undefined} className={`mx-auto flex min-w-0 w-full max-w-5xl ${message.role === 'user' ? 'justify-end gap-2' : 'justify-start'}`}>
            {message.role === 'assistant' && <PixelMascot state={pixelReplyPose(message, sending && index === messages.length - 1)} settled={message.status !== 'streaming'} className="pixel-reply-character" />}
            <div className={`min-w-0 max-w-[min(85%,48rem)] text-sm leading-6 [overflow-wrap:anywhere] ${message.publication ? 'portal-publication-response' : ''} ${message.role === 'user' ? 'rounded-[14px] px-3.5 py-2' : 'rounded-2xl px-4 py-3'} ${
              message.role === 'user'
                ? 'bg-theme-card text-theme-text'
                : message.status === 'error'
                  ? 'bg-transparent text-theme-text-secondary'
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
              {message.role === 'assistant' && <PortalGoalPlan task={message.task} active={message.status==='streaming'} disabled={isDisabled || sending || restoredActive || restoredChecking} onResume={index===messages.length-1 && !message.questions ? ()=>sendMessage(continueGoal(messages,index)) : undefined}/>}
              {message.role === 'assistant' && <PortalAgentActivity task={message.task} active={message.status === 'streaming'} status={message.status}/> }
              {message.role === 'assistant' && message.content ? (
                <>
                  {message.publication && <PixelSnapshotChanges preview={message.publication} before={message.beforePublication} variant="summary" onPreview={()=>openPublication(message.publication,'preview')} onReview={path=>openPublication(message.publication,'review',path)}/>}
                  {!message.questions && (displayedContent || message.status==='streaming') && (message.status==='error' ? <PortalResponseError content={displayedContent}/> : <PortalStreamingText key={message.revealResponse || chatIdRef.current} active={message.status==='streaming'} animate={Boolean(message.revealResponse) || message.status==='streaming'} instant={['stopped','recovering'].includes(message.status)} onReveal={chatScroll.onContentResize} components={MARKDOWN_COMPONENTS}>{displayedContent}</PortalStreamingText>)}
                  <OperationsApprovalCard content={message.content} />
                  {!message.questions && message.status!=='streaming' && <PortalResponseActions content={displayedContent}/>}
                </>
              ) : (
                <span className="break-words whitespace-pre-wrap">{message.content}</span>
              )}
              {message.role === 'assistant' && message.questions && <PixelQuestions questions={message.questions} answers={message.questionDraft} answered={index<messages.length-1} disabled={message.goalMode ? message.goalState!=='waiting' : isDisabled || sending || restoredActive || restoredChecking} onChange={questionDraft=>setMessages(previous=>previous.map((item,i)=>i===index?{...item,questionDraft}:item))} onSubmit={answer=>message.goalMode ? teams.answer(message.teamId,'0',message.questionDraft) : sendMessage(message.task?.goal ? continueGoal(messages,index,answer) : answer)}/>}
              {message.goalMode && message.goalNotice && <p role="status" className="mt-3 text-xs text-amber-300">{message.goalNotice}</p>}
              {message.teamId && !(message.goalMode && ACTIVE_TEAMS.has(message.goalState)) && <button type="button" onClick={()=>openAgents({teamId:message.teamId,agentId:'0'})} className={message.goalMode?"mt-3 border-0 bg-transparent px-0 py-2 text-xs hover:underline":"mt-3 rounded-lg border border-theme-border px-3 py-2 text-xs hover:bg-theme-border/30"}>{message.goalMode?'View goal history':'View agents and conversations'}</button>}

            </div>
            {message.role === 'user' && <UserAvatar profile={profile} className="pixel-user-character"/>}
          </div>
        )})}
        <div ref={scrollRef} />
      </div>

      <div className="pixel-composer px-4 py-3 sm:px-6">
        {chatScroll.showLatest && <div className="mb-2 text-center"><button type="button" onClick={chatScroll.jumpToLatest} className="portal-jump-latest">Jump to latest</button></div>}
        <div className={`portal-glass-composer mx-auto max-w-5xl ${messages.length===0 ? 'portal-neon-prompt' : ''}`}>
          {command && <div className="mb-2 flex flex-wrap items-center gap-2 rounded-xl bg-theme-card/70 px-3 py-2 text-xs text-theme-text-secondary" role="group" aria-label="Agent team mode"><span className="font-medium text-theme-text">Agent team</span><span>Describe your task. Portal will choose the team.</span><button type="button" disabled={isDisabled} onClick={()=>setInput(command.task)} className="ml-auto whitespace-nowrap rounded px-2 py-1 hover:bg-theme-border/30">Exit team mode</button></div>}
          {goalDraft && <div className="portal-goal-mode" role="group" aria-label="Goal mode"><span>Goal</span><small>Describe the outcome. Portal will plan, work and check its progress.</small><button type="button" disabled={isDisabled} onClick={()=>setInput(goalDraft.task)}>Exit goal mode</button></div>}
          {teams.error && <p role="alert" className="text-xs text-amber-300">{teams.error}</p>}
          <div className="pixel-composer-row">
          <textarea
            ref={inputRef}
            value={command ? command.task : goalDraft ? goalDraft.task : input}
            onChange={(event) => setInput(command ? `/agents ${event.target.value}` : goalDraft ? `/goal ${event.target.value}` : event.target.value)}
            onKeyDown={(event) => {
              if (shouldSendMessage(event, sendKey.mode)) {
                event.preventDefault()
                sendMessage()
              }
            }}
            placeholder={modelSwitching ? 'Switching model…' : status === 'available'
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
          {teams.busy && teams.teams[0] ? <button type="button" onClick={()=>teams.teams[0].mode==='goal'?teams.stop(teams.teams[0].id):openAgents({teamId:teams.teams[0].id,agentId:'0'})} title={teams.teams[0].mode==='goal'?'Stop goal':'View active agent team'} className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border-0 text-theme-text"><Square size={16}/></button> : sending || restoredActive ? (
            <button
              onClick={stopStreaming}
              disabled={stopping}
              className="inline-flex h-11 w-11 items-center justify-center rounded-xl border-0 bg-theme-surface text-theme-text transition hover:bg-theme-surface-hover disabled:cursor-wait disabled:opacity-70"
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
          {contextControl.historyUnknown && <div className="portal-context-status" role="status">
            {contextControl.resolving && <Loader2 size={13} className="animate-spin" aria-hidden="true"/>}
            <span>{contextControl.recoveryNotice || 'The previous turn could not be confirmed. Resolve it before continuing; your conversation is preserved.'}</span>
            <button type="button" disabled={!contextControl.canResolve} onClick={()=>void contextControl.resolveInterrupted()}>Resolve interrupted turn</button>
          </div>}
          {contextControl.notice && <div className="portal-context-status" role={contextControl.phase==='failed'?'alert':'status'}>
            {contextControl.busy && contextControl.phase!=='unknown' && <Loader2 size={13} className="animate-spin" aria-hidden="true"/>}
            <span>{contextControl.notice}</span>
            {['unknown','unavailable'].includes(contextControl.phase) && <button type="button" onClick={compactConversation}>Retry request</button>}
          </div>}
          <div className="pixel-composer-secondary">
            <PixelComposerTools input={input} disabled={isDisabled} onInsert={insertComposerText} onCompact={compactConversation}>
              <PixelTextFileInput key={`file-input-${chatIdRef.current}`} input={input} disabled={isDisabled} limit={MAX_INPUT_LEN} onInsert={insertComposerText}/>
              <PixelDraftPreview key={`draft-preview-${chatIdRef.current}`} input={command?.task ?? goalDraft?.task ?? input}/>
              <PixelKeyboardHelp sendMode={sendKey.mode}/>
            </PixelComposerTools>
            <div className="pixel-composer-limits">
              <PortalModelSelector activeModel={activeModel} runtimeSource={agentRuntime?.source} busy={sending || restoredActive || restoredChecking || stopping || teams.busy || contextControl.busy || status!=='available'} onSwitchingChange={setModelSwitching} onSettled={()=>setModelStatusRefresh(value=>value+1)}/>
              <PortalContextRing capacityLabel={activeContext} context={contextControl.context} capacity={contextControl.observedCapacity || agentRuntime?.contextLength} pending={sending || restoredActive || contextControl.busy} onRefresh={()=>void contextControl.refresh()}/>
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
            <PortalWorkspace key={chatIdRef.current} preview={currentPreview} access={previewAccess}
              task={[...messages].reverse().find(message=>message.role==='assistant')?.task} working={sending || restoredActive}
              agents={teams} renderApproval={content=><OperationsApprovalCard content={content}/>}
              before={[...messages].reverse().find(message=>message.publication?.siteId===currentPreview?.siteId)?.beforePublication || null}
              title={`Interactive ${displayName} preview`} request={workspaceRequest?.chatId===chatIdRef.current?workspaceRequest:null} onRequestHandled={handleWorkspaceRequest} refresh={previewRefresh}
              onRefresh={()=>setPreviewRefresh(value=>value+1)}
              collapsed={previewCollapsed} onCollapse={()=>setPreviewCollapsed(value=>!value)}
              expanded={workspaceExpanded} onExpand={()=>setWorkspaceExpanded(value=>!value)}
              onClose={()=>{setWorkspaceOpen(false);setWorkspaceExpanded(false)}}
              onPublish={isDisabled?undefined:()=>{setWorkspaceExpanded(false);insertComposerText('Publique o site que voce criou nesta conversa no preview do ODS. Inspecione os arquivos existentes, preserve o projeto e use pixel_ods_workspace_preview para a pasta que contem index.html.')}}/>

          </aside>
        )}
      </div>
    </div>
  )
}
