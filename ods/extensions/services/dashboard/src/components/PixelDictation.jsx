import { useEffect, useRef, useState } from 'react'
import { Mic, Square } from 'lucide-react'

export default function PixelDictation({ disabled, conversationId, onInsert }) {
  const recognition = useRef(null)
  const insert = useRef(onInsert)
  insert.current = onInsert
  const [listening, setListening] = useState(false)
  const [notice, setNotice] = useState('')
  const stop = () => {
    const active = recognition.current
    recognition.current = null
    active?.abort()
    setListening(false)
  }
  useEffect(() => () => {
    const active = recognition.current
    recognition.current = null
    active?.abort()
  }, [])
  useEffect(() => { stop() }, [disabled, conversationId])
  function start() {
    if (listening) { stop(); return }
    if (disabled) return
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
      const text = Array.from(event.results).filter(result => result.isFinal !== false).map(result => result[0]?.transcript || '').join(' ').trim()
      if (text) insert.current(`${text} `)
    }
    active.onend = () => { if (recognition.current === active) { recognition.current = null; setListening(false) } }
    active.onerror = event => {
      if (recognition.current !== active) return
      setNotice(event.error === 'not-allowed' ? 'Microphone access was not granted. Nothing was added to your message.' : 'Dictation could not finish. Your existing draft is unchanged.')
      recognition.current = null; setListening(false)
    }
    try { active.start(); setListening(true) } catch { recognition.current = null; setNotice('Dictation could not start in this browser.') }
  }
  return <div className="pixel-dictation">
    <button type="button" disabled={disabled} aria-label={listening ? 'Stop dictation' : 'Dictate message'} aria-pressed={listening} title="Browser dictation may use your browser provider’s online speech service. Audio is not sent to the ODS model." onClick={start}>{listening ? <Square size={15}/> : <Mic size={16}/>}</button>
    {listening && <span role="status">Listening…</span>}
    {notice && <span role="status" className="pixel-dictation-notice">{notice}</span>}
  </div>
}
