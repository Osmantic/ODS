import { useEffect, useId, useRef, useState } from 'react'
import { Languages, Mic, Square } from 'lucide-react'

export default function PixelDictation({ disabled, conversationId, onInsert }) {
  const recognition = useRef(null)
  const insert = useRef(onInsert)
  insert.current = onInsert
  const [listening, setListening] = useState(false)
  const [finishing, setFinishing] = useState(false)
  const [notice, setNotice] = useState('')
  const [language, setLanguage] = useState('')
  const [languageOpen, setLanguageOpen] = useState(false)
  const languageId = useId()
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
  useEffect(() => { stop() }, [disabled, conversationId])
  function start() {
    if (finishing) { stop(); setNotice('Dictation cancelled. Text already received is kept; pending speech was discarded.'); return }
    if (listening) { recognition.current?.stop(); setFinishing(true); return }
    if (disabled) return
    setLanguageOpen(false)
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) { setNotice('Dictation is not supported by this browser. You can still type your message.'); return }
    setNotice('')
    const active = new SpeechRecognition()
    recognition.current = active
    active.lang = language || navigator.language || 'en-US'
    active.interimResults = false
    active.continuous = false
    active.onresult = event => {
      if (recognition.current !== active) return
      const text = Array.from(event.results).slice(event.resultIndex ?? 0).filter(result => result.isFinal !== false).map(result => result[0]?.transcript || '').join(' ').trim()
      if (text) insert.current(`${text} `)
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
    <button type="button" disabled={disabled || listening} aria-label="Dictation language" aria-expanded={languageOpen} aria-controls={languageId} onClick={() => setLanguageOpen(value => !value)} title="Choose the spoken language"><Languages size={16}/></button>
    {languageOpen && <div id={languageId} className="pixel-dictation-notice">
      <label>Spoken language<select aria-label="Spoken language" disabled={disabled || listening} value={language} onChange={event => setLanguage(event.target.value)} className="my-2 block w-full rounded border border-theme-border bg-theme-bg p-2">
        <option value="">Browser language ({navigator.language || 'en-US'})</option>
        <option value="en-US">English (US)</option><option value="en-GB">English (UK)</option>
        <option value="vi-VN">Tiếng Việt</option><option value="es-ES">Español</option><option value="fr-FR">Français</option>
        <option value="de-DE">Deutsch</option><option value="pt-BR">Português (Brasil)</option><option value="ja-JP">日本語</option>
        <option value="ko-KR">한국어</option><option value="zh-CN">中文（简体）</option><option value="hi-IN">हिन्दी</option><option value="ar-SA">العربية</option>
      </select></label>
      <p>Applies to the next recording in this tab. Language support depends on your browser's speech service.</p>
      <button type="button" className="mt-2 underline" onClick={() => setLanguageOpen(false)}>Close language options</button>
    </div>}
    <button type="button" disabled={disabled} aria-label={finishing ? 'Cancel dictation' : listening ? 'Stop dictation' : 'Dictate message'} aria-pressed={listening} title="Browser dictation may use your browser provider’s online speech service. Audio is not sent to the ODS model." onClick={start}>{listening ? <Square size={15}/> : <Mic size={16}/>}</button>
    {listening && <span role="status">{finishing ? 'Finishing dictation…' : 'Listening…'}</span>}
    {notice && <span role="status" className="pixel-dictation-notice">{notice}</span>}
  </div>
}
