import { useEffect, useRef, useState } from 'react'
import { Paperclip } from 'lucide-react'

const EXTENSIONS = /\.(txt|md|markdown|csv|tsv|json|jsonl|yaml|yml|toml|xml|html|css|js|jsx|ts|tsx|py|sh|log)$/i
const MAX_BYTES = 16 * 1024

function quotedFile(name, text) {
  const fence = '`'.repeat(Math.max(2, ...[...text.matchAll(/`+/g)].map(match => match[0].length)) + 1)
  return `\n\nFile: ${JSON.stringify(name)}\n${fence}text\n${text}\n${fence}\n`
}

export default function PixelTextFileInput({ input, disabled, limit, onInsert }) {
  const field = useRef(null)
  const reader = useRef(null)
  const [file, setFile] = useState(null)
  const [error, setError] = useState('')
  const [reading, setReading] = useState(false)
  useEffect(() => () => { if (reader.current) { reader.current.onload = null; reader.current.onerror = null; reader.current.abort() } }, [])

  function choose(event) {
    const selected = event.target.files?.[0]
    event.target.value = ''
    if (!selected) return
    reader.current?.abort()
    setFile(null); setError(''); setReading(false)
    if (!EXTENSIONS.test(selected.name)) { setError('Choose a text, code, JSON or CSV file. PDF, images and archives are not supported here.'); return }
    if (!selected.size || selected.size > MAX_BYTES) { setError('Choose a nonempty text file no larger than 16 KB.'); return }
    const next = new globalThis.FileReader()
    reader.current = next
    setReading(true)
    next.onload = () => {
      setReading(false)
      try {
        const text = new TextDecoder('utf-8', {fatal:true}).decode(next.result)
        if (text.includes('\0')) throw new Error('Binary content')
        setFile({name:selected.name, text:quotedFile(selected.name, text)})
      } catch { setError('The file must contain valid UTF-8 text, without binary bytes.') }
    }
    next.onerror = () => { setReading(false); setError('The file could not be read. Choose it again.') }
    next.readAsArrayBuffer(selected)
  }
  const fits = file && input.length + file.text.length + 1 <= limit
  return <div className="pixel-text-file-input text-xs text-theme-text-secondary">
    <input ref={field} type="file" aria-label="Choose text file" accept=".txt,.md,.csv,.tsv,.json,.jsonl,.yaml,.yml,.toml,.xml,.html,.css,.js,.jsx,.ts,.tsx,.py,.sh,.log" hidden disabled={disabled} onChange={choose}/>
    <button type="button" aria-label="Add text file" title="Add text file" disabled={disabled || reading} onClick={() => field.current?.click()}><Paperclip size={16}/></button>
    {reading && <span role="status">Reading local file…</span>}
    {error && <p role="alert">{error}</p>}
    {file && <div role="group" aria-label="Review text file">
      <p>{file.name} · Text will be inserted into your draft. It is sent to the selected model only when you send the message.</p>
      {!fits && <p role="alert">The file and draft exceed the message limit. Shorten the draft or choose a smaller file.</p>}
      <button type="button" disabled={disabled || !fits} onClick={() => { onInsert(file.text); setFile(null) }}>Insert file text</button>
      <button type="button" onClick={() => setFile(null)}>Discard file</button>
    </div>}
  </div>
}
