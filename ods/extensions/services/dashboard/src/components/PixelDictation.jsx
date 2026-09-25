import { useEffect, useRef, useState } from 'react'
import { Mic, Square } from 'lucide-react'
import {appendComposerText} from '../lib/pixelComposerText'

export default function PixelDictation({ disabled, conversationId, onInsert, input = '', limit = 16384 }) {
  const recognition = useRef(null)
  const insert = useRef(onInsert)
  insert.current = onInsert
  const draft = useRef({input, value:input})
  if (draft.current.input !== input) draft.current = {input, value:input}
  const [listening, setListening] = useState(false)
  const [finishing, setFinishing] = useState(false)
  const [notice, setNotice] = useState('')
  const [retained, setRetained] = useState('')
  const stop = () => {
    const active = recognition.current
    recognition.current = null
    active?.abort()
    setListening(false)
    setFinishing(false)
  }
  useEffect(() => () => {
    const active = recognition.current
    recognition.current = null
    active?.abort()
  }, [])
  useEffect(() => { stop(); setRetained(''); setNotice(''); draft.current.value = draft.current.input }, [disabled, conversationId])
  function insertWithinLimit(text) {
    const next = appendComposerText(draft.current.value, text)
    if (next.length > limit) return false
    // Multiple final events may arrive before React commits the parent draft.
    draft.current.value = next
    insert.current(text)
    return true
  }
  function start() {
    if (finishing) { stop(); setNotice('Dictation cancelled. Text already received is kept; pending speech was discarded.'); return }
    if (listening) { recognition.current?.stop(); setFinishing(true); return }
    if (disabled || retained) return
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) { setNotice('Dictation is not supported by this browser. You can still type your message.'); return }
    setNotice('')
    const active = new SpeechRecognition()
    recognition.current = active
    active.lang = navigator.language || 'en-US'
    active.interimResults = false
    active.continuous = false
    active.onresult = event => {
      if (recognition.current !== active) return
      const text = Array.from(event.results).slice(event.resultIndex ?? 0).filter(result => result.isFinal !== false).map(result => result[0]?.transcript || '').join(' ').trim()
      if (text && !insertWithinLimit(`${text} `)) {
        stop()
        setNotice('Dictation would exceed the draft limit. Shorten your draft to insert the retained words, or copy them manually.')
        setRetained(`${text} `)
      }
    }
    active.onend = () => { if (recognition.current === active) { recognition.current = null; setListening(false); setFinishing(false) } }
    active.onerror = event => {
      if (recognition.current !== active) return
      setNotice(event.error === 'not-allowed' ? 'Microphone access was not granted. Nothing was added to your message.' : 'Dictation could not finish. Your existing draft is unchanged.')
      recognition.current = null; setListening(false); setFinishing(false)
    }
    try { active.start(); setListening(true) } catch { recognition.current = null; setNotice('Dictation could not start in this browser.') }
  }
  return <div className="pixel-dictation">
    <button type="button" disabled={disabled || Boolean(retained)} aria-label={finishing ? 'Cancel dictation' : listening ? 'Stop dictation' : 'Dictate message'} aria-pressed={listening} title="Browser dictation may use your browser provider’s online speech service. Audio is not sent to the ODS model." onClick={start}>{listening ? <Square size={15}/> : <Mic size={16}/>}</button>
    {listening && <span role="status">{finishing ? 'Finishing dictation…' : 'Listening…'}</span>}
    {notice && <div className="pixel-dictation-notice"><p role="status">{notice}</p>{retained && <>
      <textarea aria-label="Retained dictation" readOnly value={retained} rows={3} className="my-2 w-full rounded border border-theme-border bg-theme-bg p-2" onFocus={event => event.target.select()}/>
      <button type="button" disabled={disabled || appendComposerText(input, retained).length > limit} onClick={() => {if (insertWithinLimit(retained)) {setRetained(''); setNotice('Retained dictation added.')}}}>Insert retained dictation</button>
      <button type="button" onClick={() => {setRetained(''); setNotice('Retained dictation discarded.')}}>Discard retained dictation</button>
    </>}</div>}
  </div>
}
