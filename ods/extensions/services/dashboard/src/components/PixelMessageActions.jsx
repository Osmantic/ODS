import {useEffect, useRef, useState} from 'react'

export default function PixelMessageActions({content, role, canReuse, onReuse}) {
  const [state, setState] = useState('idle')
  const generation = useRef(0)
  const busy = useRef(false)
  const manual = useRef(null)
  useEffect(() => {generation.current++; busy.current = false; setState('idle'); return () => {generation.current++}}, [content])
  async function copy() {
    if (busy.current) return
    const current = generation.current
    busy.current = true; setState('copying')
    let timer
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
      await Promise.race([
        navigator.clipboard.writeText(content),
        new Promise((_, reject) => {timer = setTimeout(() => reject(new Error('Clipboard timed out')), 5000)}),
      ])
      if (current === generation.current) setState('copied')
    } catch {if (current === generation.current) setState('manual')}
    finally {clearTimeout(timer); if (current === generation.current) busy.current = false}
  }
  return <div className="mt-3 text-xs text-theme-text-muted" role="group" aria-label={`${role === 'user' ? 'Prompt' : 'Reply'} actions`}>
    <div className="flex flex-wrap gap-3">
      <button type="button" disabled={state === 'copying'} onClick={copy}>{state === 'copying' ? 'Copying…' : role === 'assistant' ? 'Copy Markdown' : 'Copy message'}</button>
      {role === 'user' && <button type="button" disabled={!canReuse} title={canReuse ? 'Append this prompt to the draft without sending' : 'Finish active work or shorten the draft before reusing this prompt'} onClick={() => onReuse(content)}>Reuse prompt</button>}
      {state === 'copied' && <span role="status">Copied</span>}
    </div>
    {state === 'manual' && <div>
      <p role="alert">Clipboard unavailable. Select the original text below and copy it manually.</p>
      <textarea ref={manual} aria-label="Original message text" readOnly value={content} rows={4} className="my-2 w-full rounded border border-theme-border bg-theme-bg p-2" onFocus={event => event.target.select()}/>
      <button type="button" onClick={() => {manual.current.focus(); manual.current.select()}}>Select original text</button>
    </div>}
  </div>
}
