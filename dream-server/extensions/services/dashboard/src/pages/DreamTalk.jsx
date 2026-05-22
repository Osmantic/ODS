import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  AlertCircle, CheckCircle2, Loader2, Mic, Paperclip, RefreshCw,
  Send, Volume2, VolumeX,
} from 'lucide-react'

// Hermes likes to format with markdown (bold, lists, code). Rendering it as
// HTML keeps the chat bubbles readable instead of showing raw `**` and `-`.
// react-markdown defaults to CommonMark + no raw HTML, which is the safe
// posture for content coming back from any LLM — even our trusted local one.
const MARKDOWN_COMPONENTS = {
  p: ({ children }) => <p className="break-words [&:not(:first-child)]:mt-3">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="break-words">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  h1: ({ children }) => <h1 className="my-2 text-lg font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="my-2 text-base font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="my-2 text-sm font-semibold uppercase tracking-wide">{children}</h3>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="underline decoration-zinc-400 underline-offset-2 hover:decoration-zinc-700">{children}</a>
  ),
  code: ({ inline, children }) => inline
    ? <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[13px] text-zinc-800">{children}</code>
    : <code className="block whitespace-pre-wrap break-words rounded bg-zinc-100 p-2 font-mono text-[13px] text-zinc-800">{children}</code>,
  pre: ({ children }) => <pre className="my-2 overflow-x-auto rounded bg-zinc-100">{children}</pre>,
  blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-zinc-300 pl-3 italic text-zinc-700">{children}</blockquote>,
  hr: () => <hr className="my-3 border-zinc-200" />,
}

const welcomeMessage = {
  id: 'welcome',
  role: 'assistant',
  text: "Hey, I'm Dream — your local AI buddy, running entirely on your own hardware. Nothing leaves this box.\n\nTry me: ask anything, draft an email, run some code, plan a trip, or just chat. Or hit the mic and talk to me.",
  status: 'done',
}

function makeId(prefix) {
  if (globalThis.crypto?.randomUUID) return `${prefix}-${globalThis.crypto.randomUUID()}`
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

async function parseError(resp, fallback) {
  const body = await resp.json().catch(() => ({}))
  return body.detail || fallback || `Request failed: ${resp.status}`
}

export default function DreamTalk() {
  const [messages, setMessages] = useState([welcomeMessage])
  const [input, setInput] = useState('')
  const [status, setStatus] = useState('loading')
  const [statusText, setStatusText] = useState('Connecting to Dream Talk...')
  const [sending, setSending] = useState(false)
  const [recording, setRecording] = useState(false)
  const [spokenReplies, setSpokenReplies] = useState(() => {
    try {
      return globalThis.localStorage?.getItem('dream-talk-spoken-replies') === '1'
    } catch {
      return false
    }
  })
  const [voiceState, setVoiceState] = useState({
    tts: false,
    audioMessage: false,
    liveMic: false,
  })

  const [pendingAttachment, setPendingAttachment] = useState(null)
  // {file: File, previewUrl: string|null, kind: 'image'|'text', name: string}
  // Held in state between picking a file and sending — lets the user add a
  // caption in the textarea before submitting.

  const bottomRef = useRef(null)
  const fileInputRef = useRef(null)
  const recorderRef = useRef(null)
  const recordingChunksRef = useRef([])
  const streamControllerRef = useRef(null)

  const liveMicSupported = useMemo(() => {
    return Boolean(
      typeof window !== 'undefined' &&
      window.isSecureContext &&
      navigator.mediaDevices?.getUserMedia &&
      globalThis.MediaRecorder,
    )
  }, [])

  const refreshStatus = useCallback(async () => {
    setStatus('loading')
    try {
      const resp = await fetch('/api/talk/status', { credentials: 'same-origin' })
      if (resp.status === 401) {
        setStatus('expired')
        setStatusText('Session expired. Scan the owner card again.')
        return
      }
      if (!resp.ok) throw new Error(await parseError(resp, 'Dream Talk is not ready.'))
      const data = await resp.json()
      const capabilities = data.capabilities || {}
      setVoiceState({
        tts: Boolean(capabilities.tts),
        audioMessage: Boolean(capabilities.audio_message),
        liveMic: Boolean(liveMicSupported && capabilities.audio_message),
      })
      setStatus('ready')
      setStatusText('Ready')
    } catch (err) {
      setStatus('offline')
      setStatusText(err.message || 'Dream Talk is offline.')
    }
  }, [liveMicSupported])

  useEffect(() => { refreshStatus() }, [refreshStatus])

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messages])

  useEffect(() => {
    try {
      globalThis.localStorage?.setItem('dream-talk-spoken-replies', spokenReplies ? '1' : '0')
    } catch {
      // Best-effort preference.
    }
  }, [spokenReplies])

  useEffect(() => {
    return () => {
      streamControllerRef.current?.abort()
      streamControllerRef.current = null
    }
  }, [])

  const speak = useCallback(async (text) => {
    if (!spokenReplies || !voiceState.tts || !text.trim()) return
    try {
      const body = new FormData()
      body.set('text', text)
      const resp = await fetch('/api/talk/speak', {
        method: 'POST',
        body,
        credentials: 'same-origin',
      })
      if (!resp.ok) return
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.addEventListener('ended', () => URL.revokeObjectURL(url), { once: true })
      audio.addEventListener('error', () => URL.revokeObjectURL(url), { once: true })
      await audio.play()
    } catch {
      // Audio playback is an enhancement; never interrupt text chat for it.
    }
  }, [spokenReplies, voiceState.tts])

  const sendText = useCallback(async (text, { transcriptId = null, attachment = null } = {}) => {
    const clean = text.trim()
    // Allow an attachment with no text — the backend supplies a default
    // prompt for images ("Describe what you see in this image."). Without
    // either a caption or an attachment, there's nothing to send.
    if (!clean && !attachment) return
    if (sending || status === 'expired') return
    setSending(true)

    const userId = transcriptId || makeId('user')
    const assistantId = makeId('assistant')
    if (!transcriptId) {
      // Show the user's bubble with the image preview inline (if any) and
      // the caption text. The previewUrl is a blob: URL, valid until the
      // component unmounts or we revoke it; the browser GCs it for us when
      // the message is removed from state.
      setMessages(items => [...items, {
        id: userId,
        role: 'user',
        text: clean,
        imageUrl: attachment?.kind === 'image' ? attachment.previewUrl : null,
        fileName: attachment?.kind === 'text' ? attachment.name : null,
        status: 'done',
      }])
    }
    setMessages(items => [...items, { id: assistantId, role: 'assistant', text: '', status: 'pending' }])
    setInput('')
    // Clear the pending-attachment slot now that we've moved it into the
    // chat thread. Don't revoke the blob URL yet — the user message bubble
    // is still rendering from it.
    if (attachment) setPendingAttachment(null)

    // Live-streamed reply via SSE. The endpoint emits one JSON object per
    // ``data:`` frame: {type: "session" | "delta" | "complete" | "error" | "done"}.
    // We append delta text into the assistant bubble as each frame arrives, then
    // finalise the bubble on the ``complete`` frame. The ``done`` frame is
    // always last (even after an error) so the loop terminates cleanly.
    let assembled = ''
    let finalWarning = null
    let errorDetail = null

    // AbortController so navigating away / re-sending mid-flight cancels the
    // in-flight stream. Server-side the bridge stops pulling from llama-server
    // when the connection drops, freeing the slot for the next request.
    const controller = new AbortController()
    streamControllerRef.current?.abort()
    streamControllerRef.current = controller
    try {
      // Two endpoints + body shapes — same SSE response shape on both, so
      // the consumption loop below is unchanged.
      let resp
      if (attachment) {
        const form = new FormData()
        form.set('file', attachment.file, attachment.name)
        form.set('text', clean)
        resp = await fetch('/api/talk/attachment', {
          method: 'POST',
          // Don't set Content-Type — fetch fills in multipart/form-data
          // with the right boundary string automatically.
          headers: { 'Accept': 'text/event-stream' },
          credentials: 'same-origin',
          body: form,
          signal: controller.signal,
        })
      } else {
        resp = await fetch('/api/talk/message/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
          credentials: 'same-origin',
          body: JSON.stringify({ text: clean }),
          signal: controller.signal,
        })
      }
      if (resp.status === 401) {
        setStatus('expired')
        setStatusText('Session expired. Scan the owner card again.')
        throw new Error('Session expired.')
      }
      if (!resp.ok || !resp.body) {
        throw new Error(await parseError(resp, 'Hermes did not answer.'))
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      // SSE frames are separated by a blank line (\n\n). Buffer partial frames
      // across reads — chunked transport can split mid-frame. Lines starting
      // with ``:`` are SSE comments (keepalives); we discard them by filtering
      // for ``data:`` only below.
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let sepIdx
        while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sepIdx)
          buffer = buffer.slice(sepIdx + 2)
          const dataLines = frame.split('\n').filter(line => line.startsWith('data:'))
          if (dataLines.length === 0) continue
          const json = dataLines.map(l => l.slice(5).trimStart()).join('\n')
          let payload
          try {
            payload = JSON.parse(json)
          } catch {
            continue
          }
          if (payload.type === 'delta' && typeof payload.text === 'string') {
            assembled += payload.text
            const snapshot = assembled
            // Once token deltas start arriving the spinner caption is no
            // longer useful — clear it so the assistant bubble shows the
            // live text instead. `status: null` signals MessageBubble to
            // render the accumulated text rather than a spinner.
            setMessages(items => items.map(item =>
              item.id === assistantId
                ? { ...item, text: snapshot, status: 'pending', statusLabel: null, statusTool: null, statusDetail: null }
                : item,
            ))
          } else if (payload.type === 'status') {
            // Friendly progress caption from the bridge (e.g. "Searching the
            // web…"). Replaces the default "Thinking…" while a tool is in
            // flight. label=null means "tool done; flip back to default."
            const label = typeof payload.label === 'string' ? payload.label : null
            const tool = typeof payload.tool === 'string' ? payload.tool : null
            const detail = typeof payload.detail === 'string' ? payload.detail : null
            setMessages(items => items.map(item =>
              item.id === assistantId
                ? { ...item, statusLabel: label, statusTool: tool, statusDetail: detail }
                : item,
            ))
          } else if (payload.type === 'complete') {
            if (typeof payload.text === 'string' && payload.text) assembled = payload.text
            finalWarning = payload.warning || null
          } else if (payload.type === 'error') {
            errorDetail = payload.detail || 'Hermes did not finish the response.'
          }
        }
      }

      if (errorDetail) throw new Error(errorDetail)
      const reply = assembled || 'I did not get a response back.'
      setMessages(items => items.map(item =>
        item.id === assistantId
          ? { ...item, text: reply, status: 'done', warning: finalWarning }
          : item,
      ))
      speak(reply)
    } catch (err) {
      if (err.name === 'AbortError') {
        // User-initiated cancellation. Drop the placeholder bubble silently.
        setMessages(items => items.filter(item => item.id !== assistantId))
      } else {
        setMessages(items => items.map(item =>
          item.id === assistantId
            ? { ...item, text: err.message || 'Something went wrong.', status: 'error' }
            : item,
        ))
      }
    } finally {
      if (streamControllerRef.current === controller) {
        streamControllerRef.current = null
      }
      setSending(false)
    }
  }, [sending, speak, status])

  const sendAudioFile = useCallback(async (file) => {
    if (!file || sending || status === 'expired') return
    setSending(true)
    const userId = makeId('voice')
    const assistantId = makeId('assistant')
    setMessages(items => [
      ...items,
      { id: userId, role: 'user', text: 'Voice message', status: 'pending' },
      { id: assistantId, role: 'assistant', text: '', status: 'pending' },
    ])

    try {
      const body = new FormData()
      body.set('file', file, file.name || 'dream-talk-audio.webm')
      const resp = await fetch('/api/talk/audio-message', {
        method: 'POST',
        body,
        credentials: 'same-origin',
      })
      if (resp.status === 401) {
        setStatus('expired')
        setStatusText('Session expired. Scan the owner card again.')
        throw new Error('Session expired.')
      }
      if (!resp.ok) throw new Error(await parseError(resp, 'Voice message could not be sent.'))
      const data = await resp.json()
      const reply = data.text || 'I did not get a response back.'
      setMessages(items => items.map(item => {
        if (item.id === userId) return { ...item, text: data.transcript || 'Voice message', status: 'done' }
        if (item.id === assistantId) return { ...item, text: reply, status: 'done', warning: data.warning || null }
        return item
      }))
      speak(reply)
    } catch (err) {
      setMessages(items => items.map(item => {
        if (item.id === userId) return { ...item, status: 'error' }
        if (item.id === assistantId) return { ...item, text: err.message || 'Something went wrong.', status: 'error' }
        return item
      }))
    } finally {
      setSending(false)
    }
  }, [sending, speak, status])

  const startRecording = async () => {
    if (!voiceState.liveMic || recording || sending) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      recordingChunksRef.current = []
      recorder.ondataavailable = event => {
        if (event.data?.size) recordingChunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        stream.getTracks().forEach(track => track.stop())
        const blob = new Blob(recordingChunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        if (blob.size > 0) {
          sendAudioFile(new File([blob], 'recording.webm', { type: blob.type || 'audio/webm' }))
        }
      }
      recorderRef.current = recorder
      recorder.start()
      setRecording(true)
    } catch {
      setVoiceState(current => ({ ...current, liveMic: false }))
    }
  }

  const stopRecording = () => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive') return
    recorder.stop()
    recorderRef.current = null
    setRecording(false)
  }

  const handleAttachmentPicked = useCallback((file) => {
    if (!file || sending || status === 'expired') return
    const isImage = (file.type || '').startsWith('image/')
    const previewUrl = isImage ? URL.createObjectURL(file) : null
    setPendingAttachment(prev => {
      // Revoke a previous blob URL before swapping in a new one so the
      // browser can GC the old image bytes.
      if (prev?.previewUrl) URL.revokeObjectURL(prev.previewUrl)
      return { file, previewUrl, kind: isImage ? 'image' : 'text', name: file.name || 'attachment' }
    })
  }, [sending, status])

  const clearPendingAttachment = useCallback(() => {
    setPendingAttachment(prev => {
      if (prev?.previewUrl) URL.revokeObjectURL(prev.previewUrl)
      return null
    })
  }, [])

  const submit = (event) => {
    event.preventDefault()
    if (pendingAttachment) {
      sendText(input, { attachment: pendingAttachment })
    } else {
      sendText(input)
    }
  }

  const retryLast = () => {
    const lastUser = [...messages].reverse().find(message => message.role === 'user' && message.status !== 'pending')
    if (lastUser) sendText(lastUser.text)
  }

  // Send is enabled either with text OR an attachment (an image alone is a
  // valid message — the model gets a default "describe this" prompt).
  const canSend = (input.trim().length > 0 || pendingAttachment) && !sending && status !== 'expired'

  return (
    <div className="min-h-dvh bg-[#f8faf8] text-zinc-950 antialiased">
      <div className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col">
        <header className="sticky top-0 z-10 border-b border-zinc-200 bg-[#f8faf8]/95 px-4 py-3 backdrop-blur">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-base font-semibold leading-tight tracking-normal">Dream Talk</h1>
              <div className="mt-1 flex items-center gap-1.5 text-xs text-zinc-500">
                {status === 'ready' ? <CheckCircle2 size={13} className="text-emerald-600" /> : null}
                {status === 'loading' ? <Loader2 size={13} className="animate-spin" /> : null}
                {status === 'offline' || status === 'expired' ? <AlertCircle size={13} className="text-amber-600" /> : null}
                <span>{statusText}</span>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setSpokenReplies(value => !value)}
                className={`grid h-10 w-10 place-items-center rounded-full border ${
                  spokenReplies ? 'border-emerald-300 bg-emerald-50 text-emerald-700' : 'border-zinc-200 bg-white text-zinc-500'
                }`}
                aria-label={spokenReplies ? 'Turn spoken replies off' : 'Turn spoken replies on'}
                title={spokenReplies ? 'Spoken replies on' : 'Spoken replies off'}
              >
                {spokenReplies ? <Volume2 size={18} /> : <VolumeX size={18} />}
              </button>
              <button
                type="button"
                onClick={refreshStatus}
                className="grid h-10 w-10 place-items-center rounded-full border border-zinc-200 bg-white text-zinc-500"
                aria-label="Refresh Dream Talk status"
                title="Refresh"
              >
                <RefreshCw size={17} />
              </button>
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto px-4 py-4">
          <div className="space-y-3">
            {messages.map(message => (
              <MessageBubble key={message.id} message={message} />
            ))}
            <div ref={bottomRef} />
          </div>
        </main>

        {status === 'expired' && (
          <div className="mx-4 mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            This owner session ended. Scan the owner card again to continue.
          </div>
        )}

        {status === 'offline' && (
          <div className="mx-4 mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900">
            Dream Talk cannot reach its local services. Try again after the box finishes starting.
          </div>
        )}

        {typeof window !== 'undefined' && !window.isSecureContext && (
          <div className="mx-4 mb-3 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs text-zinc-600">
            Live mic needs HTTPS. Text chat still works here, and your phone may offer native audio capture.
          </div>
        )}

        <form onSubmit={submit} className="sticky bottom-0 border-t border-zinc-200 bg-[#f8faf8]/95 p-3 backdrop-blur">
          {/* Attachment preview strip — appears above the input bar between
              pick and send. Shows a thumbnail for images, a generic icon for
              text/code files, with an X to discard. */}
          {pendingAttachment && (
            <AttachmentPreview
              attachment={pendingAttachment}
              onClear={() => clearPendingAttachment()}
            />
          )}
          <div className="flex items-end gap-2 rounded-[1.75rem] border border-zinc-200 bg-white p-2 shadow-sm">
            <input
              ref={fileInputRef}
              type="file"
              // Image-first attach UX. ``capture`` is deliberately NOT set —
              // its presence makes iOS Safari open the camera/video recorder
              // instead of the file/photo picker, which was the cause of the
              // earlier "paperclip opens video mode" UX bug. Accept list is
              // narrow on purpose: images for vision, common code/text files
              // for inline context. PDFs/docx need a parser dependency we
              // haven't added yet.
              accept="image/*,.txt,.md,.markdown,.csv,.json,.yaml,.yml,.log,.py,.js,.ts,.tsx,.jsx,.html,.css,.sh"
              className="hidden"
              onChange={event => {
                const file = event.target.files?.[0]
                event.target.value = ''
                if (file) handleAttachmentPicked(file)
              }}
              aria-label="Attach image or file"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={sending || status === 'expired' || pendingAttachment}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-zinc-500 disabled:opacity-40"
              aria-label="Attach image or file"
              title="Attach image or file"
            >
              <Paperclip size={19} />
            </button>
            <textarea
              value={input}
              onChange={event => setInput(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  if (canSend) sendText(input)
                }
              }}
              rows={1}
              placeholder="Message Dream Server"
              className="max-h-32 min-h-11 flex-1 resize-none bg-transparent px-1 py-2.5 text-[16px] leading-6 text-zinc-950 outline-none placeholder:text-zinc-400"
              disabled={status === 'expired'}
            />
            {voiceState.liveMic ? (
              <button
                type="button"
                onClick={recording ? stopRecording : startRecording}
                disabled={sending}
                className={`grid h-11 w-11 shrink-0 place-items-center rounded-full ${
                  recording ? 'bg-red-600 text-white' : 'bg-zinc-100 text-zinc-700'
                } disabled:opacity-40`}
                aria-label={recording ? 'Stop recording' : 'Record voice'}
                title={recording ? 'Stop recording' : 'Record'}
              >
                {recording ? <Loader2 size={18} className="animate-spin" /> : <Mic size={18} />}
              </button>
            ) : null}
            <button
              type="submit"
              disabled={!canSend}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-zinc-950 text-white disabled:bg-zinc-200 disabled:text-zinc-400"
              aria-label="Send message"
              title="Send"
            >
              {sending ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
            </button>
          </div>
          {messages.some(message => message.status === 'error') && (
            <div className="mt-2 flex justify-end">
              <button type="button" onClick={retryLast} className="text-sm font-medium text-zinc-700">
                Retry last message
              </button>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}

function MessageBubble({ message }) {
  const user = message.role === 'user'
  return (
    <div className={`flex ${user ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[84%] rounded-2xl px-4 py-3 text-[15px] leading-6 shadow-sm ${
          user
            ? 'rounded-br-md bg-zinc-950 text-white'
            : message.status === 'error'
              ? 'rounded-bl-md border border-red-200 bg-red-50 text-red-900'
              : 'rounded-bl-md border border-zinc-200 bg-white text-zinc-900'
        }`}
      >
        {message.status === 'pending' && !message.text ? (
          // While the assistant is working but hasn't streamed any tokens
          // yet, the spinner shows a dynamic caption sourced from the
          // bridge's tool events ("Searching the web…", "Running code…",
          // etc.). Falls back to "Thinking…" when no tool is active.
          // statusDetail (e.g. the search query) is rendered below the
          // caption when present, so the user can see *what* is being
          // searched/read, not just "doing something."
          <span className="inline-flex flex-col gap-0.5 text-zinc-500">
            <span className="inline-flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" />
              {message.statusLabel || 'Thinking…'}
            </span>
            {message.statusDetail && (
              <span className="ml-6 text-xs text-zinc-400">{message.statusDetail}</span>
            )}
          </span>
        ) : user || message.status === 'error' ? (
          // User messages and error bubbles stay as plain text — markdown
          // in those contexts would let typos render as headings or
          // accidental bold, which we don't want.
          <>
            {message.imageUrl && (
              <img
                src={message.imageUrl}
                alt="Attached"
                className="mb-2 max-h-72 max-w-full rounded-lg object-contain"
              />
            )}
            {message.fileName && !message.imageUrl && (
              <p className="mb-2 inline-flex items-center gap-1.5 rounded-md bg-zinc-800/80 px-2 py-1 text-xs">
                <Paperclip size={12} />{message.fileName}
              </p>
            )}
            {message.text && <p className="whitespace-pre-wrap break-words">{message.text}</p>}
          </>
        ) : (
          <div className="space-y-0 text-[15px] leading-6">
            <ReactMarkdown components={MARKDOWN_COMPONENTS}>{message.text}</ReactMarkdown>
          </div>
        )}
        {message.warning && (
          <p className="mt-2 text-xs text-amber-600">{message.warning}</p>
        )}
      </div>
    </div>
  )
}

function AttachmentPreview({ attachment, onClear }) {
  // Strip rendered above the input bar between picking a file and pressing
  // Send. Lets the user see what they're about to attach + add a caption +
  // back out cleanly with the X button.
  const isImage = attachment.kind === 'image'
  return (
    <div className="mb-2 flex items-center gap-2 rounded-xl border border-zinc-200 bg-white px-2 py-1.5 shadow-sm">
      {isImage ? (
        <img
          src={attachment.previewUrl}
          alt="Attachment preview"
          className="h-12 w-12 shrink-0 rounded-md object-cover"
        />
      ) : (
        <div className="grid h-12 w-12 shrink-0 place-items-center rounded-md bg-zinc-100 text-zinc-500">
          <Paperclip size={18} />
        </div>
      )}
      <div className="min-w-0 flex-1 text-sm">
        <p className="truncate font-medium text-zinc-900">{attachment.name}</p>
        <p className="text-xs text-zinc-500">{isImage ? 'Image' : 'File'}</p>
      </div>
      <button
        type="button"
        onClick={onClear}
        className="grid h-8 w-8 place-items-center rounded-full text-zinc-500 hover:bg-zinc-100"
        aria-label="Remove attachment"
        title="Remove"
      >
        ✕
      </button>
    </div>
  )
}
