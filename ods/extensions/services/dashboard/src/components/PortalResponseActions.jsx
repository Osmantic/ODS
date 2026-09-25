import {useEffect, useRef, useState} from 'react'
import {Check, Copy} from 'lucide-react'
import './portal-response-actions.css'

// The caller supplies the same substantive Markdown shown in the response.
// Publication receipts, tool events and other message metadata are not inputs.
export default function PortalResponseActions({content}) {
  const text = typeof content === 'string' ? content : ''
  const [state, setState] = useState('idle')
  const revision = useRef(0)
  const busy = useRef(false)
  const timeout = useRef(null)
  const feedback = useRef(null)

  useEffect(() => {
    revision.current++
    busy.current = false
    setState('idle')
    return () => {
      revision.current++
      clearTimeout(timeout.current)
      clearTimeout(feedback.current)
    }
  }, [text])

  function copy() {
    if (busy.current || !text.trim()) return
    const current = ++revision.current
    busy.current = true
    clearTimeout(feedback.current)
    setState('copying')

    function finish(result) {
      if (current !== revision.current) return
      revision.current++
      clearTimeout(timeout.current)
      busy.current = false
      setState(result)
      if (result === 'copied') {
        const copiedRevision = revision.current
        feedback.current = setTimeout(() => {
          if (copiedRevision === revision.current) setState('idle')
        }, 2000)
      }
    }

    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
      timeout.current = setTimeout(() => finish('failed'), 5000)
      Promise.resolve(navigator.clipboard.writeText(text)).then(
        () => finish('copied'),
        () => finish('failed'),
      )
    } catch { finish('failed') }
  }

  if (!text.trim()) return null
  const title = state === 'copied' ? 'Copied' : state === 'copying' ? 'Copying…' : state === 'failed' ? 'Try copying again' : 'Copy response'
  return <div className="portal-response-actions" role="group" aria-label="Response actions">
    <button type="button" className="portal-response-copy" aria-label="Copy response" aria-busy={state === 'copying'} title={title} disabled={state === 'copying'} onClick={copy}>
      {state === 'copied' ? <Check size={15} aria-hidden="true"/> : <Copy size={15} aria-hidden="true"/>}
    </button>
    <span className="portal-response-copy-status" role="status">{state === 'copied' ? 'Response copied' : ''}</span>
    {state === 'failed' && <span className="portal-response-copy-error" role="alert">Couldn’t copy. Try again or select the response to copy it manually.</span>}
  </div>
}
